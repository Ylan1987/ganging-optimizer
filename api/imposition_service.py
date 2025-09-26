# app/services/imposition_service.py
import fitz  # PyMuPDF
from typing import List, Dict, Any
import base64

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

            width_match = abs(pdf_width_mm - expected_width) < 1
            height_match = abs(pdf_height_mm - expected_height) < 1
            rotated_width_match = abs(pdf_width_mm - expected_height) < 1
            rotated_height_match = abs(pdf_height_mm - expected_width) < 1

            if not ((width_match and height_match) or (rotated_width_match and rotated_height_match)):
                error_msg = f"Las dimensiones del TrimBox ({pdf_width_mm:.1f}x{pdf_height_mm:.1f}mm) no coinciden con las esperadas ({expected_width}x{expected_height}mm)."
                return {"isValid": False, "errorMessage": error_msg}
            
            pix = page.get_pixmap(dpi=30, clip=trimbox)

            is_original_landscape = trimbox.width > trimbox.height
            is_placement_landscape = expected_width > expected_height
            
            if is_original_landscape != is_placement_landscape:
                mat = fitz.Matrix(0, 1, -1, 0, pix.height, 0)
                pix = fitz.Pixmap(pix, mat)
            
            img_bytes = pix.tobytes("png")
            base64_img = base64.b64encode(img_bytes).decode('utf-8')

            return {
                "isValid": True,
                "previewImage": f"data:image/png;base64,{base64_img}"
            }

    except Exception as e:
        return {"isValid": False, "errorMessage": f"Error al procesar el PDF: {str(e)}"}


def validate_and_create_imposition(sheet_config: Dict, jobs: List[Dict], job_files: Dict) -> bytes:
    """
    Valida las dimensiones de los PDFs subidos y crea el pliego impuesto.
    """
    # 1. Validación de Dimensiones (sin cambios)
    for job in jobs:
        # ... (código de validación se mantiene igual) ...

    # 2. Creación del Pliego (sin cambios)
    sheet_width_pt = sheet_config['width'] * (72 / 25.4)
    sheet_height_pt = sheet_config['length'] * (72 / 25.4)
    final_doc = fitz.open()
    final_page = final_doc.new_page(width=sheet_width_pt, height=sheet_height_pt)

    # 3. Estampado de los trabajos
    for job in jobs:
        job_name = job['job_name']
        pdf_content = job_files[job_name]
        placements = job['placements']
        
        with fitz.open(stream=pdf_content, filetype="pdf") as source_doc:
            source_page = source_doc[0]
            source_trimbox = source_page.trimbox
            is_source_landscape = source_trimbox.width > source_trimbox.height

            # --- INICIO DE LA CORRECCIÓN ---
            # La lógica de rotación se mueve DENTRO del bucle que itera por cada 'pos' (placement)
            for pos in placements:
                # Se calcula la orientación para CADA casilla individualmente
                is_placement_landscape = pos['width'] > pos['length']
                
                rotation_angle = 0
                if is_source_landscape != is_placement_landscape:
                    rotation_angle = 90
                
                x_pt = pos['x'] * (72 / 25.4)
                y_pt = pos['y'] * (72 / 25.4)
                
                dest_width_pt = pos['width'] * (72 / 25.4)
                dest_height_pt = pos['length'] * (72 / 25.4)
                rect = fitz.Rect(x_pt, y_pt, x_pt + dest_width_pt, y_pt + dest_height_pt)
                
                final_page.show_pdf_page(rect, source_doc, 0, rotate=rotation_angle)
            # --- FIN DE LA CORRECCIÓN ---
    
    return final_doc.tobytes()