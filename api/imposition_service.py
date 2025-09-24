# app/services/imposition_service.py
import fitz  # PyMuPDF
from typing import List, Dict, Any

def validate_and_create_imposition(sheet_config: Dict, jobs: List[Dict], job_files: Dict) -> bytes:
    """
    Valida las dimensiones de los PDFs subidos y crea el pliego impuesto.
    """
    # 1. Validación de Dimensiones
    for job in jobs:
        job_name = job['job_name']
        expected_dims = job['trim_box']
        
        if job_name not in job_files:
            raise ValueError(f"Falta el archivo PDF para el trabajo: {job_name}")

        pdf_content = job_files[job_name]
        
        with fitz.open(stream=pdf_content, filetype="pdf") as doc:
            page = doc[0]
            trim_box = page.trimbox
            
            width_mm = round(trim_box.width * (25.4 / 72), 1)
            height_mm = round(trim_box.height * (25.4 / 72), 1)

            # Damos un margen de +/- 1mm por posibles errores de redondeo
            if not (abs(width_mm - expected_dims['width']) < 1 and abs(height_mm - expected_dims['height']) < 1):
                raise ValueError(f"Las dimensiones del PDF para '{job_name}' ({width_mm}x{height_mm}mm) no coinciden con las guardadas ({expected_dims['width']}x{expected_dims['height']}mm).")

    # 2. Creación del Pliego
    sheet_width_pt = sheet_config['width'] * (72 / 25.4)
    sheet_height_pt = sheet_config['height'] * (72 / 25.4)
    
    final_doc = fitz.open()
    final_page = final_doc.new_page(width=sheet_width_pt, height=sheet_height_pt)

    # 3. Estampado de los trabajos
    for job in jobs:
        job_name = job['job_name']
        pdf_content = job_files[job_name]
        placements = job['placements']
        
        with fitz.open(stream=pdf_content, filetype="pdf") as source_doc:
            source_page = source_doc[0]
            for pos in placements:
                x_pt = pos['x'] * (72 / 25.4)
                y_pt = pos['y'] * (72 / 25.4)
                rect = fitz.Rect(x_pt, y_pt, x_pt + source_page.trimbox.width, y_pt + source_page.trimbox.height)
                final_page.show_pdf_page(rect, source_doc, 0)
    
    return final_doc.tobytes()