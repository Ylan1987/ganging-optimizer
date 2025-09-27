# Nombre del archivo: imposition_service.py
# ESTADO: CORREGIDO

import fitz  # PyMuPDF
from typing import List, Dict, Any
import base64
import traceback
import logging

# Configuramos el logging para que sea simple y se muestre en Vercel
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

def validate_and_preview_pdf(pdf_content: bytes, expected_width: float, expected_height: float, bleed_mm: float) -> Dict:
    """
    Valida las dimensiones del TrimBox de un PDF y genera una imagen de previsualización.
    """
    try:
        logging.info("Iniciando validación de PDF...")
        logging.info(f"Dimensiones esperadas del placement (con sangrado): {expected_width}x{expected_height}, Sangrado: {bleed_mm}")

        with fitz.open(stream=pdf_content, filetype="pdf") as doc:
            if not doc or len(doc) == 0: raise ValueError(f"El PDF para '{job_name}' está vacío.")
            
            page = doc[0]
            logging.info("PDF abierto correctamente.")
            
            trimbox = page.trimbox
            if not trimbox:
                raise ValueError("El PDF no contiene un TrimBox definido.")
            
            # Conversión de puntos a mm (1 pulgada = 72 puntos = 25.4 mm)
            pdf_width_mm = trimbox.width * (25.4 / 72)
            pdf_height_mm = trimbox.height * (25.4 / 72)
            logging.info(f"TrimBox detectado en PDF: {pdf_width_mm:.2f}x{pdf_height_mm:.2f} mm")

            ### CORRECCIÓN 1: CÁLCULO EXPLÍCITO Y CORRECTO DEL TRIMBOX ESPERADO ###
            # Se calcula el tamaño final esperado restando únicamente el sangrado.
            # Esto elimina cualquier posible resta adicional (como el "-2" que causaba el error a 148mm).
            expected_trim_width = expected_width - (2 * bleed_mm)
            expected_trim_height = expected_height - (2 * bleed_mm)
            logging.info(f"TrimBox esperado (calculado sin sangrado): {expected_trim_width:.2f}x{expected_trim_height:.2f} mm")

            ### CORRECCIÓN 2: LÓGICA DE COMPARACIÓN ROBUSTA CON TOLERANCIA ###
            # Se comprueban ambas orientaciones (original y rotada) con una tolerancia de 1mm.
            # Esto asegura que el PDF original (p. ej. 100x150) coincida con el placement rotado (150x100).
            tolerance = 1.0  # Tolerancia de 1mm para la comparación

            # Comprobación 1: Coincidencia directa (sin rotar)
            match_as_is = (abs(pdf_width_mm - expected_trim_width) < tolerance and
                           abs(pdf_height_mm - expected_trim_height) < tolerance)

            # Comprobación 2: Coincidencia rotada
            match_rotated = (abs(pdf_width_mm - expected_trim_height) < tolerance and
                             abs(pdf_height_mm - expected_trim_width) < tolerance)

            if not (match_as_is or match_rotated):
                error_msg = f"Las dimensiones del TrimBox del PDF ({pdf_width_mm:.1f}x{pdf_height_mm:.1f}mm) no coinciden con las esperadas para el placement ({expected_trim_width:.1f}x{expected_trim_height:.1f}mm)."
                logging.warning(f"Validación fallida: {error_msg}")
                # Se devuelve un diccionario claro para que el front-end pueda mostrar el error.
                return {"isValid": False, "errorMessage": error_msg}
            
            logging.info("Validación de dimensiones exitosa.")

            # La lógica de previsualización y rotación de la imagen ya era correcta.
            rotation_angle = 0
            is_original_landscape = trimbox.width > trimbox.height
            is_placement_landscape = expected_width > expected_height
            if is_original_landscape!= is_placement_landscape:
                rotation_angle = 90
            
            logging.info(f"Rotación necesaria para la previsualización: {rotation_angle} grados.")
            
            mat = fitz.Matrix().prerotate(rotation_angle)
            pix = page.get_pixmap(dpi=72, clip=trimbox, matrix=mat)
            logging.info("Previsualización de imagen generada.")
            
            img_bytes = pix.tobytes("png")
            base64_img = base64.b64encode(img_bytes).decode('utf-8')

            return {
                "isValid": True,
                "previewImage": f"data:image/png;base64,{base64_img}"
            }

    except Exception as e:
        tb_str = traceback.format_exc()
        logging.error(f"--- ERROR INESPERADO EN validate_and_preview_pdf ---\n{tb_str}\n--------------------")
        # Devuelve un mensaje genérico para no exponer detalles internos en producción.
        return {"isValid": False, "errorMessage": f"Error interno del servidor al procesar el PDF."}


def validate_and_create_imposition(sheet_config: Dict, jobs: List, job_files: Dict) -> bytes:
    # Esta función no se modifica, ya que el problema está en la validación previa.
    # Se incluye para mantener el archivo completo.
    # 1. Validación de Dimensiones
    for job in jobs:
        job_name = job['job_name']
        expected_dims = job['trim_box']
        if job_name not in job_files: raise ValueError(f"Falta el archivo PDF para el trabajo: {job_name}")
        pdf_content = job_files[job_name]
        with fitz.open(stream=pdf_content, filetype="pdf") as doc:
            if not doc or len(doc) == 0: raise ValueError(f"El PDF para '{job_name}' está vacío.")
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
            source_page = source_doc
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
                rotation_angle = 90 if is_source_landscape!= is_placement_landscape else 0
                
                x_pt = (pos['x'] * (72 / 25.4)) + x_offset
                y_pt = (pos['y'] * (72 / 25.4)) + y_offset
                dest_width_pt = pos['width'] * (72 / 25.4)
                dest_height_pt = pos['length'] * (72 / 25.4)
                rect = fitz.Rect(x_pt, y_pt, x_pt + dest_width_pt, y_pt + dest_height_pt)
                
                logging.info(f"--- Colocando '{job_name}' en el pliego ---")
                logging.info(f"  Coordenadas del Rect (en mm):")
                logging.info(f"    - x0: {rect.x0 / (72 / 25.4):.2f}")
                logging.info(f"    - y0: {rect.y0 / (72 / 25.4):.2f}")
                logging.info(f"  Dimensiones del Rect (en mm):")
                logging.info(f"    - Ancho: {rect.width / (72 / 25.4):.2f}")
                logging.info(f"    - Alto: {rect.height / (72 / 25.4):.2f}")
                logging.info(f"  Rotación aplicada: {rotation_angle} grados")

                final_page.show_pdf_page(rect, source_doc, 0, rotate=rotation_angle)

                trim_w_pt, trim_h_pt = trimbox.width, trimbox.height
                if rotation_angle == 90:
                    trim_w_pt, trim_h_pt = trim_h_pt, trim_w_pt
                
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