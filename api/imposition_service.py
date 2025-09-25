# app/services/imposition_service.py
import fitz  # PyMuPDF
from typing import List, Dict, Any
import base64


# (La función validate_and_create_imposition que ya tenías se mantiene igual)
def validate_and_preview_pdf(pdf_content: bytes, expected_width: float, expected_height: float) -> Dict:
    """
    Valida las dimensiones del TrimBox de un PDF y genera una imagen de previsualización.
    """
    try:
        with fitz.open(stream=pdf_content, filetype="pdf") as doc:
            if not doc:
                raise ValueError("No se pudo abrir el archivo PDF.")
            
            page = doc[0]
            
            # 1. Obtener y validar el TrimBox
            trimbox = page.trimbox
            if not trimbox:
                raise ValueError("El PDF no contiene un TrimBox definido.")

            pdf_width_pt = trimbox.width
            pdf_height_pt = trimbox.height

            pdf_width_mm = pdf_width_pt * (25.4 / 72)
            pdf_height_mm = pdf_height_pt * (25.4 / 72)

            # 2. Comparar dimensiones (permitiendo rotación)
            width_match = abs(pdf_width_mm - expected_width) < 1
            height_match = abs(pdf_height_mm - expected_height) < 1
            rotated_width_match = abs(pdf_width_mm - expected_height) < 1
            rotated_height_match = abs(pdf_height_mm - expected_width) < 1

            if not ((width_match and height_match) or (rotated_width_match and rotated_height_match)):
                error_msg = f"Las dimensiones del TrimBox ({pdf_width_mm:.1f}x{pdf_height_mm:.1f}mm) no coinciden con las esperadas ({expected_width}x{expected_height}mm)."
                return {"isValid": False, "errorMessage": error_msg}

            # 3. Determinar si la imagen de previsualización necesita rotación
            # Queremos que la orientación de la imagen coincida con la del pliego.
            pdf_is_landscape = pdf_width_mm > pdf_height_mm
            placement_is_landscape = expected_width > expected_height
            rotation = 0
            if pdf_is_landscape != placement_is_landscape:
                rotation = 90
            
            # 4. Generar la imagen de previsualización desde el TrimBox
            # Usamos una resolución de 150 DPI para la vista previa
            zoom = 150 / 72 
            mat = fitz.Matrix(zoom, zoom).prerotate(rotation)
            
            # Recortamos la imagen al TrimBox antes de renderizar
            pix = page.get_pixmap(matrix=mat, clip=trimbox)
            
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