# app/services/imposition_service.py
import fitz  # PyMuPDF
from typing import List, Dict, Any
import base64
import traceback
import logging

# Configuramos el logging para que sea simple y se muestre en Vercel
logging.basicConfig(level=logging.INFO, format='%(message)s')

def validate_and_preview_pdf(pdf_content: bytes, expected_width: float, expected_height: float, bleed_mm: float) -> Dict:
    """
    Valida las dimensiones del TrimBox de un PDF y genera una imagen de previsualización.
    """
    try:
        with fitz.open(stream=pdf_content, filetype="pdf") as doc:
            if not doc:
                raise ValueError("No se pudo abrir el archivo PDF.")
            
            page = doc[0]
            
            trimbox = page.trimbox
            if not trimbox:
                raise ValueError("El PDF no contiene un TrimBox definido.")

            pdf_width_pt = trimbox.width
            pdf_height_pt = trimbox.height
            pdf_width_mm = pdf_width_pt * (25.4 / 72)
            pdf_height_mm = pdf_height_pt * (25.4 / 72)

            expected_trim_width = expected_width - (2 * bleed_mm)
            expected_trim_height = expected_height - (2 * bleed_mm)

            width_match = abs(pdf_width_mm - expected_trim_width) < 1
            height_match = abs(pdf_height_mm - expected_trim_height) < 1
            rotated_width_match = abs(pdf_width_mm - expected_trim_height) < 1
            rotated_height_match = abs(pdf_height_mm - expected_trim_width) < 1

            if not ((width_match and height_match) or (rotated_width_match and rotated_height_match)):
                error_msg = f"Las dimensiones del TrimBox ({pdf_width_mm:.1f}x{pdf_height_mm:.1f}mm) no coinciden con las esperadas ({expected_trim_width:.1f}x{expected_trim_height:.1f}mm)."
                return {"isValid": False, "errorMessage": error_msg}
            
            rotation_angle = 0
            is_original_landscape = trimbox.width > trimbox.height
            is_placement_landscape = expected_width > expected_height
            if is_original_landscape != is_placement_landscape:
                rotation_angle = 90
            
            mat = fitz.Matrix().prerotate(rotation_angle)
            
            pix = page.get_pixmap(dpi=72, clip=trimbox, matrix=mat)
            
            img_bytes = pix.tobytes("png")
            base64_img = base64.b64encode(img_bytes).decode('utf-8')

            return {
                "isValid": True,
                "previewImage": f"data:image/png;base64,{base64_img}"
            }

    except Exception as e:
        tb_str = traceback.format_exc()
        logging.error(f"--- ERROR DETALLADO EN validate_and_preview_pdf ---\n{tb_str}\n--------------------")
        return {"isValid": False, "errorMessage": f"Error al procesar el PDF: {str(e)}"}


def validate_and_create_imposition(sheet_config: Dict, jobs: List, job_files: Dict) -> bytes:
    """
    Valida, centra, impone los trabajos con sangrado y dibuja marcas de corte profesionales.
    """
    # 1. Validación de Dimensiones
    for job in jobs:
        job_name = job['job_name']
        expected_dims = job['trim_box']
        if job_name not in job_files: raise ValueError(f"Falta el archivo PDF para el trabajo: {job_name}")
        pdf_content = job_files[job_name]
        with fitz.open(stream=pdf_content, filetype="pdf") as doc:
            page = doc[0]
            trim_box = page.trimbox
            if not trim_box: raise ValueError(f"El PDF para '{job_name}' no contiene un TrimBox definido.")
            width_mm, height_mm = trim_box.width * (25.4 / 72), trim_box.height * (25.4 / 72)
            expected_width, expected_height = expected_dims['width'], expected_dims['height']
            match_as_is = abs(width_mm - expected_width) < 1 and abs(height_mm - expected_height) < 1
            match_rotated = abs(width_mm - expected_height) < 1 and abs(height_mm - expected_width) < 1
            if not (match_as_is or match_rotated):
                raise ValueError(f"Las dimensiones del PDF para '{job_name}' ({width_mm:.1f}x{height_mm:.1f}mm) no coinciden con las esperadas ({expected_width}x{expected_height}mm).")

    # 2. CÁLCULO DE CENTRADO Y CREACIÓN DEL PLIEGO
    max_x_pt, max_y_pt = 0, 0
    for job in jobs:
        for pos in job['placements']:
            x_pt = pos['x'] * (72 / 25.4)
            y_pt = pos['y'] * (72 / 25.4)
            width_pt = pos['width'] * (72 / 25.4)
            length_pt = pos['length'] * (72 / 25.4)
            if (x_pt + width_pt) > max_x_pt: max_x_pt = x_pt + width_pt
            if (y_pt + length_pt) > max_y_pt: max_y_pt = y_pt + length_pt

    sheet_width_pt = sheet_config['width'] * (72 / 25.4)
    sheet_height_pt = sheet_config['length'] * (72 / 25.4)
    x_offset = (sheet_width_pt - max_x_pt) / 2 if max_x_pt < sheet_width_pt else 0
    y_offset = (sheet_height_pt - max_y_pt) / 2 if max_y_pt < sheet_height_pt else 0

    logging.info("--- CÁLCULO DE CENTRADO (en mm) ---")
    logging.info(f"Max(X) Ocupado: {max_x_pt / (72 / 25.4):.2f}")
    logging.info(f"Max(Y) Ocupado: {max_y_pt / (72 / 25.4):.2f}")
    logging.info(f"Margen_x: {x_offset / (72 / 25.4):.2f}")
    logging.info(f"Margen_y: {y_offset / (72 / 25.4):.2f}")
    logging.info("-------------------------------------")

    final_doc = fitz.open()
    final_page = final_doc.new_page(width=sheet_width_pt, height=sheet_height_pt)
    cut_coords_x, cut_coords_y = set(), set()

    # 3. ESTAMPADO Y RECOLECCIÓN DE COORDENADAS DE CORTE
    for job in jobs:
        job_name = job['job_name']
        pdf_content = job_files[job_name]
        placements = job['placements']
        user_bleed_mm = job['trim_box']['bleed']
        user_bleed_pt = user_bleed_mm * (72 / 25.4)

        with fitz.open(stream=pdf_content, filetype="pdf") as source_doc:
            source_page = source_doc[0]
            trimbox = source_page.trimbox
            is_source_landscape = trimbox.width > trimbox.height

            source_page.set_cropbox(fitz.Rect(
                trimbox.x0 - user_bleed_pt,
                trimbox.y0 - user_bleed_pt,
                trimbox.x1 + user_bleed_pt,
                trimbox.y1 + user_bleed_pt
            ))

            for pos in placements:
                is_placement_landscape = pos['width'] > pos['length']
                rotation_angle = 90 if is_source_landscape != is_placement_landscape else 0
                
                x_inicial_mm = pos['x']
                y_inicial_mm = pos['y']
                x_final_mm = (x_inicial_mm * (72 / 25.4) + x_offset) / (72 / 25.4)
                y_final_mm = (y_inicial_mm * (72 / 25.4) + y_offset) / (72 / 25.4)
                
                logging.info(f"\n--- Colocando Trabajo: {job_name} ---")
                logging.info(f"  x_inicial: {x_inicial_mm:.2f} mm")
                logging.info(f"  x_final:   {x_final_mm:.2f} mm")
                logging.info(f"  y_inicial: {y_inicial_mm:.2f} mm")
                logging.info(f"  y_final:   {y_final_mm:.2f} mm")

                x_pt = (pos['x'] * (72 / 25.4)) + x_offset
                y_pt = (pos['y'] * (72 / 25.4)) + y_offset
                dest_width_pt = pos['width'] * (72 / 25.4)
                dest_height_pt = pos['length'] * (72 / 25.4)
                rect = fitz.Rect(x_pt, y_pt, x_pt + dest_width_pt, y_pt + dest_height_pt)
                
                final_page.show_pdf_page(rect, source_doc, 0, rotate=rotation_angle)

                # Se calculan las dimensiones del TrimBox después de la rotación
                trim_w_pt, trim_h_pt = trimbox.width, trimbox.height
                if rotation_angle == 90:
                    trim_w_pt, trim_h_pt = trim_h_pt, trim_w_pt
                
                # Se calculan las esquinas basándose en el centro del rectángulo de destino
                center_x = rect.x0 + dest_width_pt / 2
                center_y = rect.y0 + dest_height_pt / 2
                
                tl_x = center_x - trim_w_pt / 2
                tl_y = center_y - trim_h_pt / 2
                br_x = center_x + trim_w_pt / 2
                br_y = center_y + trim_h_pt / 2

                cut_coords_x.add(tl_x); cut_coords_x.add(br_x)
                cut_coords_y.add(tl_y); cut_coords_y.add(br_y)

    # 4. DIBUJO DE MARCAS DE CORTE PROFESIONALES
    mark_len = 14
    mark_color = (0, 0, 0)
    mark_width = 0.3

    for x in sorted(list(cut_coords_x)):
        final_page.draw_line(fitz.Point(x, y_offset - mark_len), fitz.Point(x, y_offset), color=mark_color, width=mark_width)
        final_page.draw_line(fitz.Point(x, max_y_pt + y_offset), fitz.Point(x, max_y_pt + y_offset + mark_len), color=mark_color, width=mark_width)
    for y in sorted(list(cut_coords_y)):
        final_page.draw_line(fitz.Point(x_offset - mark_len, y), fitz.Point(x_offset, y), color=mark_color, width=mark_width)
        final_page.draw_line(fitz.Point(max_x_pt + x_offset, y), fitz.Point(max_x_pt + x_offset + mark_len, y), color=mark_color, width=mark_width)
    
    return final_doc.tobytes()