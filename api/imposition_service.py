# app/services/imposition_service.py
import fitz  # PyMuPDF
from typing import List, Dict, Any
import base64
import traceback

def validate_and_preview_pdf(pdf_content: bytes, expected_width: float, expected_height: float) -> Dict:
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

            # Calculamos las dimensiones esperadas del TrimBox restando el sangrado
            expected_trim_width = expected_width - (2 * bleed_mm)
            expected_trim_height = expected_height - (2 * bleed_mm)

            # Comparamos el TrimBox del PDF con el TrimBox esperado
            width_match = abs(pdf_width_mm - expected_trim_width) < 1
            height_match = abs(pdf_height_mm - expected_trim_height) < 1
            rotated_width_match = abs(pdf_width_mm - expected_trim_height) < 1
            rotated_height_match = abs(pdf_height_mm - expected_trim_width) < 1

            if not ((width_match and height_match) or (rotated_width_match and rotated_height_match)):
                error_msg = f"Las dimensiones del TrimBox ({pdf_width_mm:.1f}x{pdf_height_mm:.1f}mm) no coinciden con las esperadas ({expected_trim_width:.1f}x{expected_trim_height:.1f}mm)."
                return {"isValid": False, "errorMessage": error_msg}
                
            # 1. Decidimos el ángulo de rotación necesario (0 o 90 grados)
            is_original_landscape = pdf_width_mm > pdf_height_mm
            is_placement_landscape = expected_width > expected_height
            rotation_angle = 0
            if is_original_landscape != is_placement_landscape:
                rotation_angle = 90
            
            # 2. Creamos una matriz de transformación que SOLO contiene la rotación.
            mat = fitz.Matrix().prerotate(rotation_angle)
            
            # 3. Generamos la imagen en un solo paso, pasándole el recorte (clip)
            #    y la matriz de rotación. Este es el método más robusto y compatible.
            pix = page.get_pixmap(dpi=72, clip=trimbox, matrix=mat)
            
            # --- FIN DE LA CORRECCIÓN ---
            
            img_bytes = pix.tobytes("png")
            base64_img = base64.b64encode(img_bytes).decode('utf-8')

            return {
                "isValid": True,
                "previewImage": f"data:image/png;base64,{base64_img}"
            }

    except Exception as e:
        tb_str = traceback.format_exc()
        print(f"--- ERROR DETALLADO EN validate_and_preview_pdf ---\n{tb_str}\n--------------------")
        return {"isValid": False, "errorMessage": f"Error al procesar el PDF: {str(e)}"}


def validate_and_create_imposition(sheet_config: Dict, jobs: List, job_files: Dict) -> bytes:
    """
    Valida las dimensiones de los PDFs subidos y crea el pliego impuesto.
    """
    for job in jobs:
        job_name = job['job_name']
        expected_dims = job['trim_box']
        
        if job_name not in job_files:
            raise ValueError(f"Falta el archivo PDF para el trabajo: {job_name}")

        pdf_content = job_files[job_name]
        
        with fitz.open(stream=pdf_content, filetype="pdf") as doc:
            page = doc[0]
            trim_box = page.trimbox
            
            if not trim_box:
                 raise ValueError(f"El PDF para '{job_name}' no contiene un TrimBox definido.")
            
            width_mm = trim_box.width * (25.4 / 72)
            height_mm = trim_box.height * (25.4 / 72)

            expected_width = expected_dims['width']
            expected_height = expected_dims['height']
            
            match_as_is = abs(width_mm - expected_width) < 1 and abs(height_mm - expected_height) < 1
            match_rotated = abs(width_mm - expected_height) < 1 and abs(height_mm - expected_width) < 1
            
            if not (match_as_is or match_rotated):
                error_msg = (
                    f"Las dimensiones del PDF para '{job_name}' ({width_mm:.1f}x{height_mm:.1f}mm) "
                    f"no coinciden con las esperadas ({expected_width}x{expected_height}mm), ni siquiera al rotarlo."
                )
                raise ValueError(error_msg)

    sheet_width_pt = sheet_config['width'] * (72 / 25.4)
    sheet_height_pt = sheet_config['length'] * (72 / 25.4)
    
    final_doc = fitz.open()
    final_page = final_doc.new_page(width=sheet_width_pt, height=sheet_height_pt)

    for job in jobs:
        job_name = job['job_name']
        pdf_content = job_files[job_name]
        placements = job['placements']
        
        with fitz.open(stream=pdf_content, filetype="pdf") as source_doc:
            source_page = source_doc[0]
            
            source_trimbox = source_page.trimbox
            is_source_landscape = source_trimbox.width > source_trimbox.height
            
            # AHORA
            # Definimos las propiedades de las marcas de corte
            mark_len = 8  # 8 puntos de largo (~2.8mm)
            mark_color = (0, 0, 0) # Negro
            mark_width = 0.3

            for pos in placements:
                # La lógica para decidir la rotación se mantiene igual
                is_placement_landscape = pos['width'] > pos['length']
                rotation_angle = 0
                if is_source_landscape != is_placement_landscape:
                    rotation_angle = 90
                
                # --- 1. Usar el BleedBox para estampar ---
                # Obtenemos el BleedBox. Si no existe, usamos el TrimBox como alternativa.
                bleedbox = source_page.bleedbox
                if bleedbox.is_empty:
                    source_page.set_cropbox(source_page.trimbox)
                else:
                    source_page.set_cropbox(bleedbox)
                    
                # El rectángulo de destino en el pliego (usa las dimensiones del BleedBox calculadas por el optimizador)
                x_pt = pos['x'] * (72 / 25.4)
                y_pt = pos['y'] * (72 / 25.4)
                dest_width_pt = pos['width'] * (72 / 25.4)
                dest_height_pt = pos['length'] * (72 / 25.4)
                rect = fitz.Rect(x_pt, y_pt, x_pt + dest_width_pt, y_pt + dest_height_pt)
                
                # Estampamos el contenido del PDF (el área del BleedBox)
                final_page.show_pdf_page(rect, source_doc, 0, rotate=rotation_angle)

                # --- 2. Dibujar líneas de corte en el TrimBox ---
                trimbox = source_page.trimbox
                # Calculamos el margen de sangrado en cada eje
                bleed_margin_x = (bleedbox.width - trimbox.width) / 2
                bleed_margin_y = (bleedbox.height - trimbox.height) / 2
                
                # Calculamos las 4 esquinas del TrimBox DENTRO del rectángulo de destino
                if rotation_angle == 0:
                    tl = fitz.Point(rect.x0 + bleed_margin_x, rect.y0 + bleed_margin_y)
                    br = fitz.Point(rect.x1 - bleed_margin_x, rect.y1 - bleed_margin_y)
                else: # Si está rotado, los márgenes se invierten
                    tl = fitz.Point(rect.x0 + bleed_margin_y, rect.y0 + bleed_margin_x)
                    br = fitz.Point(rect.x1 - bleed_margin_y, rect.y1 - bleed_margin_x)

                tr = fitz.Point(br.x, tl.y)
                bl = fitz.Point(tl.x, br.y)
                
                # Dibujamos las 8 líneas de corte
                final_page.draw_line(fitz.Point(tl.x - mark_len, tl.y), tl, color=mark_color, width=mark_width)
                final_page.draw_line(fitz.Point(tl.x, tl.y - mark_len), tl, color=mark_color, width=mark_width)
                final_page.draw_line(tr, fitz.Point(tr.x + mark_len, tr.y), color=mark_color, width=mark_width)
                final_page.draw_line(fitz.Point(tr.x, tr.y - mark_len), tr, color=mark_color, width=mark_width)
                final_page.draw_line(fitz.Point(bl.x - mark_len, bl.y), bl, color=mark_color, width=mark_width)
                final_page.draw_line(bl, fitz.Point(bl.x, bl.y + mark_len), color=mark_color, width=mark_width)
                final_page.draw_line(br, fitz.Point(br.x + mark_len, br.y), color=mark_color, width=mark_width)
                final_page.draw_line(br, fitz.Point(br.x, br.y + mark_len), color=mark_color, width=mark_width)
    
    return final_doc.tobytes()