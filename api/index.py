from flask import Flask, request, jsonify
import json
from .optimizer import main as run_optimizer
 
app = Flask(__name__)

@app.route('/api/optimize', methods=['POST'])
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