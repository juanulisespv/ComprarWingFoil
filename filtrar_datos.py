import csv
import json
import re
import os

csv_path = 'resultados_wallapop.csv'
output_csv = 'resultados_filtrados.csv'
output_html = 'resultados_filtrados.html'

def parse_detected_values(val_str):
    if not val_str or val_str == '[]':
        return []
    try:
        val_str = val_str.replace("'", '"')
        parsed = json.loads(val_str)
        return [float(x) for x in parsed]
    except Exception:
        matches = re.findall(r'\d+(?:\.\d+)?', val_str)
        return [float(x) for x in matches]

def filter_data():
    if not os.path.exists(csv_path):
        print(f"Error: No se encuentra {csv_path}")
        return

    filtered_rows = []
    total_count = 0
    filtered_count = 0

    with open(csv_path, 'r', encoding='utf-8') as infile:
        reader = csv.DictReader(infile, delimiter=';')
        fieldnames = reader.fieldnames
        for row in reader:
            total_count += 1
            litros = parse_detected_values(row.get('litros_detectados', ''))
            ala = parse_detected_values(row.get('ala_detectada', ''))
            
            # Condición: Wing de 6m o Tabla > 135L
            is_wing_6 = any(abs(w - 6.0) < 0.05 for w in ala)
            is_board_135 = any(l > 135 for l in litros)
            
            if is_wing_6 or is_board_135:
                filtered_rows.append(row)
                filtered_count += 1

    with open(output_csv, 'w', newline='', encoding='utf-8') as outfile:
        writer = csv.DictWriter(outfile, fieldnames=fieldnames, delimiter=';')
        writer.writeheader()
        writer.writerows(filtered_rows)

    print(f"Filtrado completado: {filtered_count} de {total_count} anuncios coinciden.")
    
    # Intentar generar el reporte HTML filtrado
    try:
        import scraper
        scraper.generate_html_report(output_csv, output_html)
        print(f"Reporte filtrado visual generado en: {output_html}")
    except Exception as e:
        print(f"No se pudo generar el HTML filtrado: {e}")

if __name__ == '__main__':
    filter_data()
