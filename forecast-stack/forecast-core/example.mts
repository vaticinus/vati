import {computeForecast} from './src/lib/forecastEngine.ts';
import {freezeForecastSpec,readForecastSnapshot} from './src/lib/forecastSnapshot.ts';
const spec={kind:'conditional',question:'Will the project finish by 2035-12-31?',resolution_date:'2035-12-31',
  dated_metric:'Completion recorded in the project register by the deadline.',partition:'Crisis or no crisis',
  branches:[{condition:'Crisis',weight:.3,p_yes:.6},{condition:'No crisis',weight:.7,p_yes:.1}]};
const result=computeForecast(spec);
if(Math.abs(result.probability-.25)>1e-12) throw new Error('Unexpected result');
const snapshot=freezeForecastSpec(spec,result);
console.log(JSON.stringify({probability:result.probability,method:result.method,
  restored_probability:readForecastSnapshot(JSON.parse(JSON.stringify(snapshot))).result?.probability},null,2));
