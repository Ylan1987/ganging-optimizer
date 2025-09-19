import json
import sys
import math
import time
from datetime import datetime
from ortools.sat.python import cp_model
import rectpack
from itertools import chain, combinations, product
from dataclasses import dataclass, field
from typing import List, Dict

# region ESTRUCTURAS DE DATOS
@dataclass
class Size:
    width: int
    length: int

@dataclass
class FactorySize:
    width: int
    length: int
    usdPerTon: float

@dataclass
class Material:
    id: int
    name: str
    grammage: int
    isSpecialMaterial: bool
    factorySizes: List[FactorySize]

@dataclass
class Job:
    id: str
    width: int
    length: int
    quantity: int
    rotatable: bool
    material: Material
    frontInks: int
    backInks: int
    isDuplex: bool
    samePlatesForBack: bool = False

@dataclass
class Overage:
    amount: int
    perInk: bool

@dataclass
class CostInfo:
    price: float
    perInk: bool = False
    perInkPass: bool = False

@dataclass
class Machine:
    id: str
    name: str
    printingBodies: int
    maxSheetSize: Size
    overage: Overage
    minImpressionsCharge: int
    setupCost: CostInfo
    washCost: CostInfo
    impressionCost: CostInfo

@dataclass
class Penalties:
    differentPressSheetPenalty: int = 0
    differentFactorySheetPenalty: int = 0
    differentMachinePenalty: int = 0

@dataclass
class Options:
    timeoutSeconds: int
    penalties: Penalties
    numberOfSolutions: int = 1

@dataclass
class AvailableCutMap:
    forPaperSize: Size
    sheetSizes: List[Size]

@dataclass
class InputData:
    options: Options
    dollarRate: float
    jobs: List[Job]
    machines: List[Machine]
    availableCuts: List[AvailableCutMap]
# endregion

# region CALLBACK PARA MÚLTIPLES SOLUCIONES
class SolutionCallback(cp_model.CpSolverSolutionCallback):
    def __init__(self, use_layout_vars, all_viable_layouts, total_cost_var, limit):
        super().__init__()
        self.use_layout_vars = use_layout_vars
        self.all_viable_layouts = all_viable_layouts
        self.total_cost_var = total_cost_var
        self.limit = limit
        self.solutions = []

    def on_solution_callback(self):
        cost = self.Value(self.total_cost_var) / 100
        log(f"  > Solución encontrada, Costo: {cost:.2f}")
        
        plan = []
        for l_id, v in self.use_layout_vars.items():
            if self.Value(v) == 1:
                layout_obj = next((l for l in self.all_viable_layouts if l['layout_id'] == l_id), None)
                if layout_obj:
                    plan.append({
                        'id': l_id,
                        'sheets': layout_obj['net_sheets'],
                        'costForThisPlanItem': round(layout_obj['total_cost'], 2)
                    })
        
        layouts_in_plan = {l['layout_id']: l for l in self.all_viable_layouts if l['layout_id'] in [p['id'] for p in plan]}
        
        self.solutions.append({
            'summary': {'gangedTotalCost': cost},
            'productionPlan': plan,
            'layouts': layouts_in_plan
        })
        
        if len(self.solutions) >= self.limit:
            self.StopSearch()

# endregion

# region LÓGICA DE CÁLCULO DE COSTOS Y NECESIDADES
def packer_grid_layout(sheet_w, sheet_h, cut_w, cut_h):
    """Calcula un grid simple y devuelve el desglose, incluyendo posiciones."""
    if cut_w <= 0 or cut_h <= 0: return {'cutsPerSheet': 0, 'positions': []}
    
    positions1, positions2 = [], []
    count1, count2 = 0, 0

    # Opción 1: Sin rotar
    if sheet_w >= cut_w and sheet_h >= cut_h:
        cols, rows = math.floor(sheet_w / cut_w), math.floor(sheet_h / cut_h)
        count1 = cols * rows
        for r in range(rows):
            for c in range(cols):
                positions1.append({'x': c * cut_w, 'y': r * cut_h, 'width': cut_w, 'length': cut_h})

    # Opción 2: Rotado
    if sheet_w >= cut_h and sheet_h >= cut_w:
        cols, rows = math.floor(sheet_w / cut_h), math.floor(sheet_h / cut_w)
        count2 = cols * rows
        for r in range(rows):
            for c in range(cols):
                positions2.append({'x': c * cut_h, 'y': r * cut_w, 'width': cut_h, 'length': cut_w})

    if count1 >= count2:
        return {'cutsPerSheet': count1, 'positions': positions1}
    else:
        return {'cutsPerSheet': count2, 'positions': positions2}

def get_printing_needs(job_details, machine):
    front_inks, back_inks = job_details.get('frontInks', 0), job_details.get('backInks', 0)
    is_duplex = job_details.get('isDuplex', False)
    
    technique = 'SIMPLEX'
    if is_duplex:
        technique = 'DUPLEX'

    passes = 0
    total_plates = 0

    # --- INICIO DE LA MODIFICACIÓN ---
    # Se añade una comprobación para machine.printingBodies. Si es None, se trata como 0 para evitar errores.
    printing_bodies = machine.printingBodies or 0

    if technique == 'SIMPLEX':
        total_plates = front_inks
        passes = math.ceil(front_inks / printing_bodies) if printing_bodies > 0 else float('inf')
    else: # DUPLEX
        total_plates = front_inks + back_inks
        passes = math.ceil(front_inks / printing_bodies) + math.ceil(back_inks / printing_bodies) if printing_bodies > 0 else float('inf')
    # --- FIN DE LA MODIFICACIÓN ---
    
    return {'technique': technique, 'totalPlates': total_plates, 'passes': passes}

def calculate_printing_cost(machine, print_needs, net_sheets):
    total_plates, passes = print_needs['totalPlates'], print_needs['passes']
    
    setup_cost = machine.setupCost.price * (total_plates if machine.setupCost.perInk else passes)
    wash_cost = machine.washCost.price * (total_plates if machine.washCost.perInk else passes)

    impression_cost = 0
    # --- INICIO DE LA MODIFICACIÓN ---
    # Se añade "or 0" para que si minImpressionsCharge es None, se use 0 en el cálculo, evitando el TypeError.
    min_charge = machine.minImpressionsCharge or 0

    if print_needs['technique'] == 'DUPLEX':
        chargeable_sheets = max(net_sheets, min_charge)
        impression_cost = ((chargeable_sheets / 1000) * machine.impressionCost.price) * 2 # Una por frente, otra por dorso
    else: # SIMPLEX
        chargeable_sheets = max(net_sheets, min_charge)
        impression_cost = (chargeable_sheets / 1000) * machine.impressionCost.price * passes
    # --- FIN DE LA MODIFICACIÓN ---
    
    return {
        'setupCost': setup_cost,
        'washCost': wash_cost,
        'impressionCost': impression_cost,
        'totalPrintingCost': setup_cost + wash_cost + impression_cost
    }

def calculate_material_needs(material, printing_sheet, total_printing_sheets, dollar_rate):
    best_factory_option = {'sheets_to_cut': float('inf')}
    for factory_size in material.factorySizes:
        plan = packer_grid_layout(factory_size.width, factory_size.length, printing_sheet.width, printing_sheet.length)
        if plan['cutsPerSheet'] > 0:
            factory_sheets_needed = math.ceil(total_printing_sheets / plan['cutsPerSheet'])
            if factory_sheets_needed < best_factory_option['sheets_to_cut']:
                best_factory_option = {'factory_size': factory_size, 'sheets_to_cut': factory_sheets_needed, 'cuttingPlan': plan}
    
    if 'factory_size' not in best_factory_option: return None

    fs = best_factory_option['factory_size']
    cost_per_sheet = (((fs.width / 1000 * fs.length / 1000) * material.grammage) / 1000 / 1000) * fs.usdPerTon
    total_cost = best_factory_option['sheets_to_cut'] * cost_per_sheet * dollar_rate
    
    return {
        'totalMaterialCost': total_cost,
        'factorySheets': {
            'size': fs,
            'quantityNeeded': best_factory_option['sheets_to_cut'],
            'cuttingPlan': best_factory_option['cuttingPlan']
        }
    }

def calculate_total_layout_cost(layout, all_jobs, machine, dollar_rate):
    if not layout['jobs']: return None
    net_sheets = max(math.ceil(all_jobs[job_id].quantity / qty) for job_id, qty in layout['jobs'].items() if qty > 0)
    if net_sheets == 0: return None

    details = {'frontInks': 0, 'backInks': 0, 'isDuplex': False, 'material': None}
    for job_id in layout['jobs']:
        job = all_jobs[job_id]
        details.update({
            'frontInks': max(details['frontInks'], job.frontInks),
            'backInks': max(details['backInks'], job.backInks),
            'isDuplex': details['isDuplex'] or job.isDuplex,
            'material': job.material
        })

    print_needs = get_printing_needs(details, machine)
    overage_sheets = machine.overage.amount * (print_needs['totalPlates'] if machine.overage.perInk else 1)
    total_printing_sheets = net_sheets + overage_sheets
    
    material_needs = calculate_material_needs(details['material'], layout['printing_sheet'], total_printing_sheets, dollar_rate)
    if not material_needs: return None
    
    printing_cost_breakdown = calculate_printing_cost(machine, print_needs, net_sheets)
    total_cost = material_needs['totalMaterialCost'] + printing_cost_breakdown['totalPrintingCost']
    
    return {
        'total_cost': total_cost,
        'net_sheets': net_sheets,
        'machine': machine,
        'printing_sheet': layout['printing_sheet'],
        'costBreakdown': {
            'materialCost': material_needs['totalMaterialCost'],
            'printingCost': printing_cost_breakdown
        },
        'materialNeeds': material_needs,
        'printNeeds': print_needs
    }
# endregion

# region FASES DEL ALGORITMO
def get_cuts_for_factory_size(factory_size, available_cuts_maps):
    for cut_map in available_cuts_maps:
        fs_map = cut_map.forPaperSize
        if {fs_map.width, fs_map.length} == {factory_size.width, factory_size.length}:
            return cut_map.sheetSizes
    return []

def calculate_base_solution(data, all_jobs):
    log("--- FASE 1: Calculando Solución Base (Trabajos Individuales) ---")
    base_layouts = []
    total_base_cost = 0
    for job in data.jobs:
        best_option = {'total_cost': float('inf')}
        for machine in data.machines:
            for factory_size in job.material.factorySizes:
                for cut in get_cuts_for_factory_size(factory_size, data.availableCuts):
                    if not (max(cut.width, cut.length) <= max(machine.maxSheetSize.width, machine.maxSheetSize.length) and \
                            min(cut.width, cut.length) <= min(machine.maxSheetSize.width, machine.maxSheetSize.length)):
                        continue
                    plan = packer_grid_layout(cut.width, cut.length, job.width, job.length)
                    if plan['cutsPerSheet'] == 0: continue
                    cost_info = calculate_total_layout_cost({'jobs': {job.id: plan['cutsPerSheet']}, 'printing_sheet': cut}, all_jobs, machine, data.dollarRate)
                    if cost_info and cost_info['total_cost'] < best_option['total_cost']:
                        best_option = cost_info
                        best_option['jobs_in_layout'] = {job.id: plan['cutsPerSheet']}
                        best_option['placements'] = plan['positions'] # Guardar placements para el output
        if best_option['total_cost'] != float('inf'):
            log(f"  > Mejor opción para '{job.id}': {best_option['net_sheets']} pliegos. Costo: {best_option['total_cost']:.2f}")
            best_option['layout_id'] = f"base_{job.id}"
            base_layouts.append(best_option)
            total_base_cost += best_option['total_cost']
    log(f"  > Costo Base Total (individual): {total_base_cost:.2f}")
    return base_layouts, total_base_cost

def generate_candidate_layouts(data: InputData, all_jobs: Dict[str, Job]):
    log("--- FASE 2: Generando layouts de ganging candidatos ---")
    start_time, champion_layouts = time.time(), []

    for i in range(2, len(data.jobs) + 1):
        for job_subset in combinations(data.jobs, i):
            material_for_gang = job_subset[0].material
            log(f"  > Probando combinación de {i} trabajos: {[j.id for j in job_subset]} en material '{material_for_gang.name}'")

            all_possible_cuts = {f"{c.width}x{c.length}": c for fs in material_for_gang.factorySizes for c in get_cuts_for_factory_size(fs, data.availableCuts)}

            for cut_key, cut in all_possible_cuts.items():
                if time.time() - start_time > data.options.timeoutSeconds:
                    log("  > Timeout alcanzado."); return champion_layouts
                log(f"    > Analizando Pliego: {cut.width}x{cut.length}...")
                
                candidates = []
                quantity_ranges_to_test = []
                job_ids_in_subset = [j.id for j in job_subset]
                
                for job in job_subset:
                    job_area = job.width * job.length
                    if job_area == 0:
                        quantity_ranges_to_test = []
                        break
                    max_qty = min(30, math.floor((cut.width * cut.length) / job_area))
                    if max_qty == 0:
                        quantity_ranges_to_test = []
                        break
                    quantity_ranges_to_test.append(range(1, max_qty + 1))
                
                if not quantity_ranges_to_test: continue

                for quantities_tuple in product(*quantity_ranges_to_test):
                    recipe = dict(zip(job_ids_in_subset, quantities_tuple))
                    
                    total_area = sum(all_jobs[jid].width * all_jobs[jid].length * qty for jid, qty in recipe.items())
                    if total_area <= (cut.width * cut.length):
                        tiraje = max(math.ceil(all_jobs[jid].quantity / qty) for jid, qty in recipe.items())
                        candidates.append({'recipe': recipe, 'tiraje': tiraje})

                if not candidates: continue

                candidates.sort(key=lambda x: x['tiraje'])
                
                log(f"      > {len(candidates)} combinaciones de área válidas encontradas. Probando con el dibujante...")

                for cand in candidates:
                    packer = rectpack.newPacker()
                    for job_id, qty in cand['recipe'].items():
                        job = all_jobs[job_id]
                        for _ in range(qty): packer.add_rect(job.width, job.length, rid=job_id)
                    packer.add_bin(cut.width, cut.length)
                    packer.pack()
                    
                    if len(packer[0]) == sum(cand['recipe'].values()):
                        log(f"      > ÉXITO con tiraje {cand['tiraje']}: {cand['recipe']}")
                        placements = [{'id': p.rid, 'x': p.x, 'y': p.y, 'width': p.width, 'length': p.height} for p in packer[0]]
                        champion_layouts.append({
                            'layout_details': {'jobs': cand['recipe'], 'printing_sheet': cut},
                            'placements': placements
                        })
                        break 
    return champion_layouts

def solve_optimal_plan(data, all_jobs, base_layouts, candidate_layouts):
    log("--- FASE 3: Resolviendo el plan de producción óptimo ---")
    all_viable_layouts = base_layouts[:]
    for i, cand in enumerate(candidate_layouts):
        for machine in data.machines:
            cut = cand['layout_details']['printing_sheet']
            if not (max(cut.width, cut.length) <= max(machine.maxSheetSize.width, machine.maxSheetSize.length) and \
                    min(cut.width, cut.length) <= min(machine.maxSheetSize.width, machine.maxSheetSize.length)):
                continue
            cost_info = calculate_total_layout_cost(cand['layout_details'], all_jobs, machine, data.dollarRate)
            if cost_info:
                cost_info.update({
                    'layout_id': f"ganging_{i}_{machine.id}",
                    'jobs_in_layout': cand['layout_details']['jobs'],
                    'placements': cand['placements']
                })
                all_viable_layouts.append(cost_info)

    if not all_viable_layouts: return None

    model = cp_model.CpModel()
    
    use_layout_vars = {layout['layout_id']: model.NewBoolVar(f"use_{layout['layout_id']}") for layout in all_viable_layouts}

    for job in data.jobs:
        produced_expr = []
        for l in all_viable_layouts:
            if job.id in l['jobs_in_layout']:
                items_produced = l['jobs_in_layout'][job.id] * l['net_sheets']
                produced_expr.append(use_layout_vars[l['layout_id']] * items_produced)
        
        if produced_expr:
            model.Add(sum(produced_expr) >= job.quantity)

    cost_expr = sum(use_layout_vars[l['layout_id']] * int(l['total_cost'] * 100) for l in all_viable_layouts)

    machines_used = {m.id: model.NewBoolVar(f"uses_m_{m.id}") for m in data.machines}
    ps_used = {f"{l['printing_sheet'].width}x{l['printing_sheet'].length}": model.NewBoolVar(f"uses_ps_{l['printing_sheet'].width}x{l['printing_sheet'].length}") for l in all_viable_layouts}
    # *** INICIO DE LA CORRECCIÓN (KeyError) ***
    fs_used = {f"{l['factory_sheet_used'].width}x{l['factory_sheet_used'].length}": model.NewBoolVar(f"uses_fs_{l['factory_sheet_used'].width}x{l['factory_sheet_used'].length}") for l in all_viable_layouts if 'factory_sheet_used' in l}
    # *** FIN DE LA CORRECCIÓN ***

    for l in all_viable_layouts:
        model.AddImplication(use_layout_vars[l['layout_id']], machines_used[l['machine'].id])
        ps_key = f"{l['printing_sheet'].width}x{l['printing_sheet'].length}"
        model.AddImplication(use_layout_vars[l['layout_id']], ps_used[ps_key])
        # *** INICIO DE LA CORRECCIÓN (KeyError) ***
        if 'factory_sheet_used' in l:
            fs_key = f"{l['factory_sheet_used'].width}x{l['factory_sheet_used'].length}"
            model.AddImplication(use_layout_vars[l['layout_id']], fs_used[fs_key])
        # *** FIN DE LA CORRECCIÓN ***

    num_machines = model.NewIntVar(0, len(machines_used), 'num_machines')
    model.Add(num_machines == sum(machines_used.values()))
    num_ps = model.NewIntVar(0, len(ps_used), 'num_ps')
    model.Add(num_ps == sum(ps_used.values()))
    num_fs = model.NewIntVar(0, len(fs_used), 'num_fs')
    model.Add(num_fs == sum(fs_used.values()))

    total_cost_var = model.NewIntVar(0, 100000000000, 'total_cost_var')
    model.Add(total_cost_var == cost_expr)

    machine_penalty_product = model.NewIntVar(-100000000000, 100000000000, 'machine_prod')
    model.AddMultiplicationEquality(machine_penalty_product, total_cost_var, num_machines - 1)
    
    ps_penalty_product = model.NewIntVar(-100000000000, 100000000000, 'ps_prod')
    model.AddMultiplicationEquality(ps_penalty_product, total_cost_var, num_ps - 1)

    fs_penalty_product = model.NewIntVar(-100000000000, 100000000000, 'fs_prod')
    model.AddMultiplicationEquality(fs_penalty_product, total_cost_var, num_fs - 1)
    
    penalties = data.options.penalties
    
    machine_penalty_scaled = model.NewIntVar(-100000000000, 100000000000, 'machine_penalty_scaled')
    model.AddDivisionEquality(machine_penalty_scaled, machine_penalty_product * penalties.differentMachinePenalty, 100)

    ps_penalty_scaled = model.NewIntVar(-100000000000, 100000000000, 'ps_penalty_scaled')
    model.AddDivisionEquality(ps_penalty_scaled, ps_penalty_product * penalties.differentPressSheetPenalty, 100)

    fs_penalty_scaled = model.NewIntVar(-100000000000, 100000000000, 'fs_penalty_scaled')
    model.AddDivisionEquality(fs_penalty_scaled, fs_penalty_product * penalties.differentFactorySheetPenalty, 100)
    
    total_penalty_cost = model.NewIntVar(-300000000000, 300000000000, 'total_penalty_cost')
    model.Add(total_penalty_cost == machine_penalty_scaled + ps_penalty_scaled + fs_penalty_scaled)
    
    model.Minimize(total_cost_var + total_penalty_cost)
    # --- #cambio1: Bucle iterativo para buscar múltiples soluciones ---
    # Se reemplaza la llamada única al solver con un bucle que busca
    # soluciones progresivamente peores, guardando cada una.
    
    found_solutions = []
    for i in range(data.options.numberOfSolutions):
        solver = cp_model.CpSolver()
        status = solver.Solve(model)

        if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            cost = solver.Value(total_cost_var)
            log(f"  > Solución #{i+1} encontrada, Costo: {cost / 100:.2f}")

            # --- #cambio2: Almacenar la solución encontrada en el formato correcto ---
            plan = []
            for l_id, v_var in use_layout_vars.items():
                if solver.Value(v_var) == 1:
                    layout_obj = next((l for l in all_viable_layouts if l['layout_id'] == l_id), None)
                    if layout_obj:
                        plan.append({
                            'id': l_id,
                            'sheets': layout_obj['net_sheets'],
                            'costForThisPlanItem': round(layout_obj['total_cost'], 2)
                        })
            
            layouts_in_plan = {l['layout_id']: l for l in all_viable_layouts if l['layout_id'] in [p['id'] for p in plan]}
            
            found_solutions.append({
                'summary': {'gangedTotalCost': cost / 100},
                'productionPlan': plan,
                'layouts': layouts_in_plan
            })

            # --- #cambio3: Añadir restricción para la siguiente búsqueda ---
            # Se añade una restricción para que la próxima solución sea
            # estrictamente más cara que la que acabamos de encontrar.
            model.Add(total_cost_var > cost)

        else:
            log("  > No se encontraron más soluciones.")
            break # Salir del bucle si el solver no encuentra más opciones

    return found_solutions
# endregion

# region PARSEO Y EJECUCIÓN
def parse_input_data(raw_data):
    options = Options(
        raw_data['options']['timeoutSeconds'], 
        Penalties(**raw_data['options']['penalties']),
        raw_data['options'].get('numberOfSolutions', 1)
    )
    
    machines = []
    for m in raw_data['machines']:
        impression_cost_data = m['impressionCost']
        impression_cost_obj = CostInfo(
            price=impression_cost_data['pricePerThousand'],
            perInkPass=impression_cost_data.get('perInkPass', False)
        )
        machines.append(Machine(
            id=m['id'], name=m['name'], printingBodies=m['printingBodies'],
            maxSheetSize=Size(**m['maxSheetSize']),
            overage=Overage(**m['overage']),
            minImpressionsCharge=m['minImpressionsCharge'],
            setupCost=CostInfo(**m['setupCost']),
            washCost=CostInfo(**m['washCost']),
            impressionCost=impression_cost_obj
        ))
    
    jobs = [Job(material=Material(factorySizes=[FactorySize(**fs) for fs in j['material']['factorySizes']], **{k:v for k,v in j['material'].items() if k != 'factorySizes'}), **{k:v for k,v in j.items() if k != 'material'}) for j in raw_data['jobs']]
    available_cuts = [
        AvailableCutMap(
            forPaperSize=Size(**ac['forPaperSize']), 
            sheetSizes=[Size(width=ss['width'], length=ss['length']) for ss in ac['sheetSizes']]
        ) for ac in raw_data['availableCuts']
    ]
    return InputData(options, raw_data['commonDetails']['dollarRate'], jobs, machines, available_cuts)

def format_layout_for_output(layout_obj):
    """Formatea un objeto de layout para el JSON de salida."""
    if not layout_obj: return {}
    return {
        "layoutId": layout_obj.get('layout_id'),
        "sheetsToPrint": layout_obj.get('net_sheets'),
        "machine": layout_obj.get('machine'),
        "printingSheet": layout_obj.get('printing_sheet'),
        "costBreakdown": layout_obj.get('costBreakdown'),
        "materialNeeds": layout_obj.get('materialNeeds'),
        "printNeeds": layout_obj.get('printNeeds'),
        "jobsInLayout": [{'id': k, 'quantityPerSheet': v} for k, v in layout_obj.get('jobs_in_layout', {}).items()],
        "placements": layout_obj.get('placements')
    }

def log(message):
    """Imprime un mensaje con timestamp."""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")


def main(input_path):
    log(f"Iniciando optimizador con el archivo: {input_path}")
    try:
        with open(input_path, 'r', encoding='utf-8-sig') as f:
            raw_data = json.load(f)
    except json.JSONDecodeError as e:
        log(f"ERROR: El archivo '{input_path}' está vacío o no es un JSON válido. Detalles: {e}")
        return
    except FileNotFoundError:
        log(f"ERROR: No se encontró el archivo '{input_path}'.")
        return

    data = parse_input_data(raw_data)
    all_jobs_map = {job.id: job for job in data.jobs}

    base_layouts, base_cost = calculate_base_solution(data, all_jobs_map)
    candidate_layouts = generate_candidate_layouts(data, all_jobs_map)
    ganged_solutions = solve_optimal_plan(data, all_jobs_map, base_layouts, candidate_layouts)

    # Preparar el output final
    # --- #cambio4: Nueva lógica para formatear el output final ---
    # Se reestructura el output para que siempre contenga la solución base
    # y una lista de las mejores soluciones de ganging encontradas.
    output = {
        'summary': {'baselineTotalCost': round(base_cost, 2)},
        'baselineSolution': {
            'total_cost': round(base_cost, 2),
            'layouts': {l['layout_id']: l for l in base_layouts}
        },
        'gangedSolutions': []
    }

    if ganged_solutions:
        better_solutions = [s for s in ganged_solutions if s['summary']['gangedTotalCost'] < base_cost]
        better_solutions.sort(key=lambda x: x['summary']['gangedTotalCost'])
        
        formatted_solutions = []
        for sol in better_solutions[:data.options.numberOfSolutions]:
            formatted_layouts = {l_id: format_layout_for_output(l_obj) for l_id, l_obj in sol['layouts'].items()}
            sol['layouts'] = formatted_layouts
            formatted_solutions.append(sol)
        
        output['gangedSolutions'] = formatted_solutions
        
        if not output['gangedSolutions']:
            log("No se encontraron soluciones de ganging que mejoren la base.")
    else:
        log("No se encontró ninguna solución de ganging.")
    
    output_filename = "/tmp/output.json"
    with open(output_filename, 'w', encoding='utf-8') as f:
        def custom_serializer(o): return o.__dict__ if hasattr(o, '__dict__') else str(o)
        json.dump(output, f, ensure_ascii=False, indent=2, default=custom_serializer)
    log("Proceso completado. La solución está en 'output.json'.")

if __name__ == '__main__':
    if len(sys.argv) != 2:
        print("Uso: python optimizer.py <ruta_del_archivo_input.json>")
        sys.exit(1)
    main(sys.argv[1])
# endregion