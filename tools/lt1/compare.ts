import * as fs from 'fs'
import Simulator from '../../src/core/Simulator.js'
import StaticInsulinPump from '../../src/core/actuators/StaticInsulinPump.js'
import CSII from '../../src/core/controllers/CSII.js'
import VirtualPatientUvaPadova from '../../src/core/models/UvaPadova_T1DMS.js'
import IdealCGM from '../../src/core/sensors/IdealCGM.js'
import Meal from '../../src/types/Meal.js'

const sim = new Simulator()
const patient = new VirtualPatientUvaPadova()
sim.setPatient(patient)
const controller = new CSII()
controller.autoConfigure(patient.getPatientProfile())
sim.setController(controller)
sim.setSensor(new IdealCGM())
sim.setActuator(new StaticInsulinPump())
const meals: Meal[] = [
  { start: new Date('2022-05-01T08:00:00Z'), duration: 15, carbs: 50 },
]
sim.setMeals(meals)
sim.setOptions({ t0: new Date('2022-05-01T06:00:00Z'), tmax: new Date('2022-05-01T14:00:00Z') })
const results = sim.runSimulation()
const out = results.map((r) => ({ t: r.t.toISOString(), Gp: r.y?.Gp, Gt: r.y?.Gt }))
fs.writeFileSync('./compare.json', JSON.stringify(out))
console.log('points', out.length, 'first', out[0], 'last', out[out.length - 1])
