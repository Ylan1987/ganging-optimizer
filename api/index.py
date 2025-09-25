from flask import Flask, request, jsonify, send_file
import json
import io
from .optimizer import main as run_optimizer #otro comentario nuevo
from . import imposition_service 
from flask_cors import CORS

app = Flask(__name__)

origins = [
    "https://optimizador-ganging-ui.vercel.app",
    "http://localhost:3000",
    "http://localhost:5173" # Generalmente usado por Svelte/Vite
]

CORS(app, 
    resources={r"/api/*": {
        "origins": origins,
        "methods": ["GET", "POST", "OPTIONS"],
        "allow_headers": ["Content-Type", "x-vercel-protection-bypass"],
        "supports_credentials": True
    }}
)

@app.route('/api/validate-and-preview-pdf', methods=['POST', 'OPTIONS'])
def validate_and_preview_endpoint():
    if 'file' not in request.files:
        return jsonify({"error": "No se recibió ningún archivo."}), 400
    if 'expected_width' not in request.form or 'expected_height' not in request.form:
        return jsonify({"error": "Faltan las dimensiones esperadas."}), 400

    file = request.files['file']
    pdf_content = file.read()
    
    try:
        expected_width = float(request.form['expected_width'])
        expected_height = float(request.form['expected_height'])
    except ValueError:
        return jsonify({"error": "Las dimensiones deben ser números."}), 400
    
    # Llama a la función del servicio que procesa el PDF
    result = imposition_service.validate_and_preview_pdf(
        pdf_content=pdf_content,
        expected_width=expected_width,
        expected_height=expected_height
    )
    
    if not result.get('isValid'):
        # Devuelve un error 400 (Bad Request) si la validación falla
        return jsonify({"error": "PDF inválido", "details": result.get('errorMessage', 'Error desconocido')}), 400
        
    return jsonify(result), 200



@app.route('/api/optimize', methods=['POST', 'OPTIONS'])
def optimize_endpoint():
    try:
        # 1. Recibir el input.json del cuerpo de la petición
        input_data = request.get_json()
        if not input_data:
            return jsonify({"error": "No se recibió un input JSON válido."}), 400

        # 2. Guardar temporalmente el input para que el script lo lea
        temp_input_filename = "/tmp/input.json"
        with open(temp_input_filename, 'w', encoding='utf-8') as f:
            json.dump(input_data, f, ensure_ascii=False, indent=2)

        # 3. Ejecutar tu script de optimización
        run_optimizer(temp_input_filename)

        # 4. Leer el output.json generado por el script
        temp_output_filename = "/tmp/output.json" # Asegúrate que tu script escriba aquí
        with open(temp_output_filename, 'r', encoding='utf-8') as f:
            output_data = json.load(f)
        
        # 5. Devolver el resultado
        return jsonify(output_data), 200

    except Exception as e:
        # Manejo de errores
        return jsonify({"error": "Ocurrió un error en el servidor.", "details": str(e)}), 500

# Es importante modificar tu optimizer.py para que escriba en /tmp/output.json
# y no en el directorio local, ya que Vercel solo permite escribir en /tmp.

@app.route('/api/generate-imposition', methods=['POST', 'OPTIONS'])
def generate_imposition_endpoint():
    try:
        # 1. Recibir los datos del formulario (multipart/form-data)
        # El JSON con el plan de armado viene como un string en un campo de texto
        if 'layout_data' not in request.form:
            return jsonify({"error": "Falta el campo 'layout_data' con el plan de armado."}), 400
        
        layout_data_str = request.form['layout_data']
        layout = json.loads(layout_data_str)

        # Los archivos vienen en un diccionario especial
        files = request.files.getlist('files')
        if not files:
            return jsonify({"error": "No se recibieron archivos PDF."}), 400

        # Mapeamos los archivos por su nombre para un acceso fácil
        job_files = {file.filename: file.read() for file in files}

        # 2. Llamar a nuestro servicio de imposición
        pdf_bytes = imposition_service.validate_and_create_imposition(
            sheet_config=layout['sheet_config'],
            jobs=layout['jobs'],
            job_files=job_files
        )

        # 3. Devolver el PDF generado
        return send_file(
            io.BytesIO(pdf_bytes),
            mimetype='application/pdf',
            as_attachment=True,
            download_name='pliego_impuesto.pdf'
        )

    except ValueError as e: # Errores de validación controlados
        return jsonify({"error": "Error de validación.", "details": str(e)}), 400
    except Exception as e: # Errores inesperados
        return jsonify({"error": "Ocurrió un error en el servidor.", "details": str(e)}), 500
