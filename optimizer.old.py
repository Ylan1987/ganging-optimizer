import json
import sys
import math
from datetime import datetime
from ortools.sat.python import cp_model
import rectpack
from itertools import chain, combinations, product
from functools import reduce

# --- FUNCIONES AUXILIARES ---

def log(message):
    """Imprime un mensaje con timestamp."""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")

class SolutionPrinter(cp_model.CpSolverSolutionCallback):
    """Callback para mostrar el progreso de la solución."""
    def __init__(self, cost_variable):
        super().__init__()
        self.cost_variable = cost_variable
        self.solution_count = 0
    def on_solution_callback(self):
        self.solution_count += 1
        log(f"Solución intermedia #{self.solution_count} encontrada, Costo: {self.Value(self.cost_variable) / 100:.2f}")

def find_best_cut_from_factory(factory_size, printing_size):
    """
    Calcula el mejor plan de corte de un pliego de impresión desde un pliego de fábrica,
    incluyendo las posiciones de los cortes.
    """
    f_w, f_h = factory_size['width'], factory_size['length']
    p_w, p_h = printing_size['width'], printing_size['length']
    
    # Opción 1: Pliego de impresión sin rotar
    cols1 = math.floor(f_w / p_w) if p_w > 0 else 0
    rows1 = math.floor(f_h / p_h) if p_h > 0 else 0
    cuts1 = cols1 * rows1
    positions1 = []
    if cuts1 > 0:
        for r in range(rows1):
            for c in range(cols1):
                positions1.append({'x': c * p_w, 'y': r * p_h, 'width': p_w, 'length': p_h})

    # Opción 2: Pliego de impresión rotado
    cols2 = math.floor(f_w / p_h) if p_h > 0 else 0
    rows2 = math.floor(f_h / p_w) if p_w > 0 else 0
    cuts2 = cols2 * rows2
    positions2 = []
    if cuts2 > 0:
        for r in range(rows2):
            for c in range(cols2):
                positions2.append({'x': c * p_h, 'y': r * p_w, 'width': p_h, 'length': p_w})
    
    if cuts1 >= cuts2:
        return {"cutsPerSheet": cuts1, "positions": positions1, "wastePercentage": 0}
    else:
        return {"cutsPerSheet": cuts2, "positions": positions2, "wastePercentage": 0}

def calculate_material_cost(material, factory_size, dollar_rate):
    sheet_area_m2 = (factory_size['width'] / 1000) * (factory_size['length'] / 1000)
    sheet_weight_kg = (sheet_area_m2 * material['grammage']) / 1000
    cost_per_fs_usd = (sheet_weight_kg / 1000) * factory_size.get('usdPerTon', 0)
    return cost_per_fs_usd * dollar_rate

def get_cheapest_impression_price(machine, ps_w, ps_h):
    if not machine.get('is_offset', False) and 'price_brackets' in machine:
        cheapest_bracket_cost = float('inf')
        for bracket in machine['price_brackets']:
            b_w, b_h = bracket['constraints']['maxWid'], bracket['constraints']['maxLen']
            if (ps_w <= b_w and ps_h <= b_h) or (ps_w <= b_h and ps_h <= b_w):
                cost = bracket['sheetCost']['value']
                if bracket['sheetCost'].get('unit') == 'per_thousand': cost /= 1000
                cheapest_bracket_cost = min(cheapest_bracket_cost, cost)
        return cheapest_bracket_cost if cheapest_bracket_cost != float('inf') else 999999
    return machine.get('impressionCost', {}).get('pricePerThousand', 0) / 1000

def get_all_subsets(item_list):
    """
    Genera todos los subconjuntos de una lista de trabajos, comenzando desde r=2.
    """
    s = list(item_list)
    return chain.from_iterable(combinations(s, r) for r in range(2, len(s) + 1))

# --- PASO A: CÁLCULO DE LA SOLUCIÓN BASE ---
def calculate_baseline_solution(jobs, machines, available_cuts, materials, dollar_rate):
    log("--- PASO A: Calculando Solución Base (Trabajos Individuales) ---")
    baseline_plans = []
    total_baseline_cost = 0
    all_sheet_options = {tuple(sorted((ps['width'], ps['length']))): ps for cut_info in available_cuts for ps in cut_info['sheetSizes']}

    for job in jobs:
        log(f"  > Buscando la mejor opción para el trabajo: '{job['id']}'...")
        best_option, best_cost = None, float('inf')

        for machine in machines:
            for ps in all_sheet_options.values():
                ps_w, ps_h = ps['width'], ps['length']
                
                machine_can_handle_sheet = False
                if 'maxSheetSize' in machine:
                    max_w, max_h = machine['maxSheetSize']['width'], machine['maxSheetSize']['length']
                    if (ps_w <= max_w and ps_h <= max_h) or (ps_w <= max_h and ps_h <= max_w): machine_can_handle_sheet = True
                elif 'price_brackets' in machine:
                    for bracket in machine['price_brackets']:
                        b_w, b_h = bracket['constraints']['maxWid'], bracket['constraints']['maxLen']
                        if (ps_w <= b_w and ps_h <= b_h) or (ps_w <= b_h and ps_h <= b_w): machine_can_handle_sheet = True; break
                if not machine_can_handle_sheet: continue

                packer = rectpack.newPacker(); packer.add_bin(ps_w, ps_h)
                for _ in range(2000): packer.add_rect(job['width'], job['length'], rid=job['id'])
                packer.pack()

                if not packer or not packer[0]: cuts_per_sheet = 0
                else: cuts_per_sheet = len(packer[0])
                if cuts_per_sheet == 0: continue

                sheets_needed = math.ceil(job['quantity'] / cuts_per_sheet)
                mat = materials[job['material']['id']]
                
                best_fs_source, min_material_cost_per_ps = None, float('inf')
                for fs in mat['factorySizes']:
                    cut_plan = find_best_cut_from_factory(fs, ps)
                    if cut_plan['cutsPerSheet'] > 0:
                        cost_of_fs = calculate_material_cost(mat, fs, dollar_rate)
                        cost_per_ps = cost_of_fs / cut_plan['cutsPerSheet']
                        if cost_per_ps < min_material_cost_per_ps:
                            min_material_cost_per_ps = cost_per_ps
                            best_fs_source = {"factory_sheet": fs, "cutting_plan": cut_plan}
                
                if not best_fs_source: continue

                impression_cost = get_cheapest_impression_price(machine, ps_w, ps_h)
                fixed_cost = machine.get('setupCost',{}).get('price',0) + machine.get('washCost',{}).get('price',0)
                current_cost = fixed_cost + sheets_needed * (min_material_cost_per_ps + impression_cost)

                if current_cost < best_cost:
                    best_cost = current_cost
                    placements = [{"jobId": r.rid, "x": r.x, "y": r.y, "width": r.width, "length": r.height, "isRotated": False} for r in packer[0]]
                    best_option = {
                        "job": job, "cost": current_cost, "sheets": sheets_needed, "machine": machine, 
                        "printingSheet": ps, "cutsPerSheet": cuts_per_sheet, "materialCost": min_material_cost_per_ps, 
                        "impressionCost": impression_cost, "fixedCost": fixed_cost, "placements": placements, 
                        "factory_sheet": best_fs_source['factory_sheet'], "cutting_plan": best_fs_source['cutting_plan']
                    }
        
        if best_option:
            log(f"    * Mejor opción para '{job['id']}': {best_option['sheets']} pliegos en '{best_option['machine']['name']}' | Costo: {best_option['cost']:.2f}")
            baseline_plans.append(best_option)
            total_baseline_cost += best_cost

    log(f"  > Costo Base Total (individual): {total_baseline_cost:.2f}")
    log(f"  > Número de Layouts Base: {len(baseline_plans)}")
    return {"cost": total_baseline_cost, "layouts": len(baseline_plans), "plan": baseline_plans}

# --- FASE 1: GENERACIÓN DE LAYOUTS DE GANGING ---
def generate_layout_candidates(jobs, available_cuts, machines):
    log("--- FASE 1: Generando layouts candidatos con Búsqueda Sistemática (grupos de 2 o más) ---")
    layout_candidates = []
    layout_id_counter = 0
    jobs_by_material = {}
    for job in jobs:
        mat_id = job['material']['id']
        if mat_id not in jobs_by_material: jobs_by_material[mat_id] = []
        jobs_by_material[mat_id].append(job)

    all_printing_sheets = {}
    for machine in machines:
        if not machine.get('is_offset', False) and 'price_brackets' in machine:
            for bracket in machine['price_brackets']:
                ps = {'width': bracket['constraints']['maxWid'], 'length': bracket['constraints']['maxLen']}
                key = tuple(sorted((ps['width'], ps['length'])))
                if key not in all_printing_sheets: all_printing_sheets[key] = ps
    for cut_info in available_cuts:
        for ps in cut_info['sheetSizes']:
            key = tuple(sorted((ps['width'], ps['length'])))
            if key not in all_printing_sheets: all_printing_sheets[key] = ps

    for mat_id, job_group in jobs_by_material.items():
        for subset in get_all_subsets(job_group):
            subset = list(subset)
            log(f"  > Analizando ganging para grupo: {[j['id'] for j in subset]}")
            
            for ps_dims in all_printing_sheets.values():
                sheet_w, sheet_h, sheet_area = ps_dims['width'], ps_dims['length'], ps_dims['width'] * ps_dims['length']
                
                quantities = [j['quantity'] for j in subset]
                if not quantities: continue
                common_divisor = reduce(math.gcd, quantities) if len(quantities) > 1 else quantities[0]
                golden_ratio = [q // common_divisor for q in quantities]
                base_ratio_area = sum(job['width'] * job['length'] * ratio for job, ratio in zip(subset, golden_ratio))
                if base_ratio_area == 0: continue
                
                multiplier = math.floor(sheet_area / base_ratio_area)
                if multiplier == 0: continue
                
                upper_bound_ratio = [r * multiplier for r in golden_ratio]
                
                best_pack_so_far = [0] * len(subset)
                
                search_ranges = [range(ub, 0, -1) for ub in upper_bound_ratio]
                
                for current_combo in product(*search_ranges):
                    if all(current_combo[i] <= best_pack_so_far[i] for i in range(len(subset))):
                        continue
                    
                    rects_to_pack, total_area = [], 0
                    for i, job in enumerate(subset):
                        area, count = job['width'] * job['length'], current_combo[i]
                        total_area += area * count
                        rects_to_pack.extend([{'width': job['width'], 'length': job['length'], 'rid': job['id']}] * count)
                    
                    if total_area > sheet_area: continue
                    if not rects_to_pack: continue

                    log_counts_str = ", ".join([f"{count} x '{job['id']}'" for job, count in zip(subset, current_combo)])
                    log(f"    -> Pedido al dibujante: Pliego {sheet_w}x{sheet_h}, Petición: [{log_counts_str}]")

                    packer = rectpack.newPacker(rotation=False)
                    packer.add_bin(sheet_w, sheet_h)
                    for r in rects_to_pack: packer.add_rect(r['width'], r['length'], rid=r['rid'])
                    packer.pack()
                    
                    if packer and packer[0] and len(packer[0]) == len(rects_to_pack):
                        log(f"      ... Resultado del dibujante: ÉXITO, acomodó {len(packer[0])} piezas.")
                        best_pack_so_far = list(current_combo)
                        
                        layout_id_counter += 1
                        placements, jobs_in_layout = [], {}
                        for rect in packer[0]:
                            jobs_in_layout[rect.rid] = jobs_in_layout.get(rect.rid, 0) + 1
                            placements.append({"jobId": rect.rid, "x": rect.x, "y": rect.y, "width": rect.width, "length": rect.height, "isRotated": False})
                        front_inks, back_inks, is_duplex = max(j['frontInks'] for j in subset), max(j['backInks'] for j in subset), any(j['isDuplex'] for j in subset)
                        layout_candidates.append({
                            "layoutId": f"layout_{layout_id_counter}", "materialId": mat_id,
                            "printingSheet": {"width": sheet_w, "length": sheet_h},
                            "jobsInLayout": jobs_in_layout, "placements": placements,
                            "frontInks": front_inks, "backInks": back_inks, "isDuplex": is_duplex, "symmetry": "none"
                        })

    log(f"  > Se generaron {len(layout_candidates)} layouts candidatos para ganging.")
    return layout_candidates
    
# --- FASE 2 y 3: MODELO, RESOLUCIÓN Y FORMATEO ---
def solve_production_plan(data):
    jobs, machines, available_cuts = data['jobs'], data['availableMachines'], data['availableCuts']
    dollar_rate, penalties, options = data['commonDetails']['dollarRate'], data['options']['penalties'], data.get('options', {})
    materials = {job['material']['id']: job['material'] for job in jobs}
    
    baseline = calculate_baseline_solution(jobs, machines, available_cuts, materials, dollar_rate)
    layout_candidates = generate_layout_candidates(jobs, available_cuts, machines)
    
    log("\n--- FASE 2: Pre-calculando costos para análisis ---")
    rejected_candidates_details, precalculated_costs = [], {}
    for lc in layout_candidates:
        layout_id = lc['layoutId']
        precalculated_costs[layout_id] = []
        mat = materials[lc['materialId']]
        ps_w, ps_h = lc['printingSheet']['width'], lc['printingSheet']['length']
        min_material_cost_per_sheet = min(calculate_material_cost(mat, fs, dollar_rate) for fs in mat['factorySizes'])
        
        costs_for_this_layout = []
        for machine in machines:
            fixed_cost = machine.get('setupCost', {}).get('price', 0) + machine.get('washCost', {}).get('price', 0)
            impression_price = get_cheapest_impression_price(machine, ps_w, ps_h)
            cost_per_1000 = fixed_cost + 1000 * (min_material_cost_per_sheet + impression_price)
            costs_for_this_layout.append({"machineName": machine['name'], "costPer1000Sheets": round(cost_per_1000, 2)})
            precalculated_costs[layout_id].append({'fixed': fixed_cost, 'material': min_material_cost_per_sheet, 'impression': impression_price, 'setupCost': machine.get('setupCost', {}).get('price', 0), 'washCost': machine.get('washCost', {}).get('price', 0)})
        
        rejected_candidates_details.append({ "layoutId": lc['layoutId'], "printingSheet": lc['printingSheet'], "jobsInLayout": lc['jobsInLayout'], "potentialCosts": costs_for_this_layout })

    model = cp_model.CpModel()
    impressions_per_layout = {lc['layoutId']: model.NewIntVar(0, 50000, f"imp_{lc['layoutId']}") for lc in layout_candidates}
    layout_is_used = {lc['layoutId']: model.NewBoolVar(f"used_{lc['layoutId']}") for lc in layout_candidates}
    machine_for_layout = {lc['layoutId']: model.NewIntVar(0, len(machines) - 1, f"mach_{lc['layoutId']}") for lc in layout_candidates}

    for lc in layout_candidates:
        model.Add(impressions_per_layout[lc['layoutId']] > 0).OnlyEnforceIf(layout_is_used[lc['layoutId']])
        model.Add(impressions_per_layout[lc['layoutId']] == 0).OnlyEnforceIf(layout_is_used[lc['layoutId']].Not())

    for job in jobs:
        total_produced = sum(impressions_per_layout[lc['layoutId']] * lc['jobsInLayout'].get(job['id'], 0) for lc in layout_candidates)
        model.Add(total_produced >= job['quantity'])

    if baseline['layouts'] > 0:
        num_layouts_usados = sum(layout_is_used.values())
        model.Add(num_layouts_usados < baseline['layouts'])
        log(f"  > RESTRICCIÓN AÑADIDA: La solución debe usar menos de {baseline['layouts']} layouts.")

    log("  > Modelando la función de costos (versión final)...")
    total_cost_expr = []
    
    for lc in layout_candidates:
        layout_id = lc['layoutId']
        for i in range(len(machines)):
            costs = precalculated_costs[layout_id][i]
            is_printed_here = model.NewBoolVar(f"printed_{layout_id}_on_mach_{i}")
            model.Add(machine_for_layout[layout_id] == i).OnlyEnforceIf(is_printed_here)
            model.Add(machine_for_layout[layout_id] != i).OnlyEnforceIf(is_printed_here.Not())
            model.AddImplication(is_printed_here, layout_is_used[lc['layoutId']])
            total_cost_expr.append(int(costs['fixed'] * 100) * is_printed_here)
            costo_variable_unitario = int((costs['material'] + costs['impression']) * 100)
            costo_variable_total_layout = model.NewIntVar(0, 20000000000, f"var_cost_{layout_id}_mach_{i}")
            imp_var = model.NewIntVar(0, 50000, f'imp_var_{layout_id}_mach_{i}')
            model.Add(imp_var == impressions_per_layout[layout_id]).OnlyEnforceIf(is_printed_here)
            model.Add(imp_var == 0).OnlyEnforceIf(is_printed_here.Not())
            model.Add(costo_variable_total_layout == imp_var * costo_variable_unitario)
            total_cost_expr.append(costo_variable_total_layout)

    total_base_cost = model.NewIntVar(0, 100000000000, 'total_base_cost')
    model.Add(total_base_cost == sum(total_cost_expr))
    
    model.Minimize(total_base_cost) 

    log(f"\n--- FASE 3: Resolviendo el modelo... ---")
    solver = cp_model.CpSolver()
    timeout = options.get('timeoutSeconds', 60.0)
    log(f"Límite de tiempo establecido en {timeout} segundos.")
    solver.parameters.max_time_in_seconds = float(timeout)
    solution_printer = SolutionPrinter(total_base_cost)
    status = solver.Solve(model, solution_printer)

    baseline_plan_detailed = []
    for option in baseline['plan']:
        baseline_plan_detailed.append({
            "layoutId": f"baseline_{option['job']['id']}", "sheetsToPrint": option['sheets'],
            "machine": { "id": option['machine']['id'], "name": option['machine']['name'] },
            "printingSheet": option['printingSheet'], 
            "factorySheet": { "size": option['factory_sheet'], "cuttingPlan": option['cutting_plan'] },
            "costBreakdown": {
                "totalCostForLayout": round(option['cost'], 2), "costPerSheet": round(option['materialCost'] + option['impressionCost'], 2),
                "materialCost": round(option['materialCost'] * option['sheets'], 2),
                "printingCostDetails": { "setupCost": option['fixedCost'], "washCost": 0, "impressionCost": round(option['impressionCost'] * option['sheets'], 2), "totalPrintingCost": round(option['fixedCost'] + (option['impressionCost'] * option['sheets']), 2) }
            },
            "jobsInLayout": [{"id": option['job']['id'], "quantityPerSheet": option['cutsPerSheet']}],
            "placements": option['placements']
        })

    output = {
        "summary": {"baselineTotalCost": round(baseline['cost'], 2)},
        "baselineSolution": baseline_plan_detailed,
        "rejectedLayoutCandidates": rejected_candidates_details
    }

    if status == cp_model.OPTIMAL or status == cp_model.FEASIBLE:
        log("¡Solución de Ganging encontrada! Reconstruyendo formato...")
        final_plan = []
        for lc in layout_candidates:
            if solver.Value(layout_is_used[lc['layoutId']]):
                sheets_to_print = solver.Value(impressions_per_layout[lc['layoutId']])
                machine_idx = solver.Value(machine_for_layout[lc['layoutId']])
                machine, costs = machines[machine_idx], precalculated_costs[lc['layoutId']][machine_idx]
                total_layout_cost = (costs['fixed'] + (costs['material'] + costs['impression']) * sheets_to_print)
                final_plan.append({
                    "layoutId": lc['layoutId'], "sheetsToPrint": sheets_to_print,
                    "machine": { "id": machine['id'], "name": machine['name'] },
                    "printingSheet": lc['printingSheet'], "factorySheet": { "size": "N/A", "cuttingPlan": "N/A" },
                    "costBreakdown": {
                        "totalCostForLayout": round(total_layout_cost, 2), "costPerSheet": round(costs['material'] + costs['impression'], 2),
                        "materialCost": round(costs['material'] * sheets_to_print, 2),
                        "printingCostDetails": { "setupCost": costs['setupCost'], "washCost": costs['washCost'], "impressionCost": round(costs['impression'] * sheets_to_print, 2), "totalPrintingCost": round(costs['fixed'] + (costs['impression'] * sheets_to_print), 2) }
                    },
                    "jobsInLayout": [{"id": k, "quantityPerSheet": v} for k, v in lc['jobsInLayout'].items()], "placements": lc['placements']
                })
        output["optimalGangedSolution"] = {"productionPlan": final_plan, "summary": {"totalCost": solver.Value(total_base_cost) / 100.0}}
    else:
        log("No se encontró una solución de ganging que mejore la base.")
        output["optimalGangedSolution"] = {"error": "No se encontró una solución de ganging viable bajo las restricciones dadas."}
    
    return output

if __name__ == '__main__':
    if len(sys.argv) != 2: print("Uso: python optimizer.py <ruta_del_archivo_input.json>"); sys.exit(1)
    input_file, output_file = sys.argv[1], 'output.json'
    try:
        with open(input_file, 'r', encoding='utf-8') as f: data = json.load(f)
        solution = solve_production_plan(data)
        log(f"Proceso completado. Escribiendo solución en: {output_file}")
        with open(output_file, 'w', encoding='utf-8') as f: json.dump(solution, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log(f"Ocurrió un error inesperado: {e}")
        import traceback
        traceback.print_exc()