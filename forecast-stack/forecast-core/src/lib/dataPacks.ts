/** Small, inspectable public-data packs. No keys, paid services or model calls. */
import {fetchSourceBytes,packet,type EvidencePacket,type ResearchOptions} from './evidence.ts';
import {sha256} from './runtime.ts';
import {validateQuestion,type ForecastQuestion} from './workflow.ts';

export type PackConfig =
  | {kind:'weather';station:string;target_date:string;threshold_c:number}
  | {kind:'macro';series:string;target_month:string;resolution_date:string;threshold:number;unit:string}
  | {kind:'world';start:string;end:string;minimum_magnitude:number};
export type DataPack = {schema_version:1;config:PackConfig;question:ForecastQuestion;evidence:EvidencePacket;
  snapshots:{url:string;captured_at:string;sha256:string;body:string}[];metadata:{source:string;terms:string;time_semantics:string;limitations:string[];baseline_sample_size:number}};
const day=86400000;
const iso=(n:number)=>new Date(n).toISOString();
const date=(s:string)=>/^\d{4}-\d{2}-\d{2}$/.test(s)&&Number.isFinite(Date.parse(s))&&iso(Date.parse(s)).slice(0,10)===s;
const finite=(n:unknown):n is number=>typeof n==='number'&&Number.isFinite(n);
const jeffreys=(successes:number,n:number)=>(successes+.5)/(n+1);

export async function collectDataPack(config:PackConfig,options:Pick<ResearchOptions,'fetch'|'resolve'> & {now?:Date}={}):Promise<DataPack>{
  const now=options.now??new Date(),today=now.toISOString().slice(0,10),snapshots:DataPack['snapshots']=[];
  const get=async(url:string)=>{
    const response=await fetchSourceBytes(url,{...options,fetch:(input,init)=>(options.fetch??fetch)(input,{...init,headers:{...init?.headers,Accept:'application/geo+json,application/json'}})}),body=response.bytes.toString('utf8');
    const captured_at=options.now?.toISOString()??new Date().toISOString();
    snapshots.push({url:response.url,captured_at,sha256:sha256(response.bytes),body});return JSON.parse(body);
  };
  let question:ForecastQuestion,summary:object,metadata:DataPack['metadata'];
  if(config.kind==='weather'){
    if(!/^[A-Z0-9]{3,8}$/.test(config.station)||!date(config.target_date)||config.target_date<=today||!finite(config.threshold_c))throw new Error('Weather needs a station, future UTC target date and Celsius threshold');
    const end=Date.parse(today),start=end-10*day;
    const observations=new Map<string,{timestamp:string;temperature:number}>();
    // Some station responses ignore a small limit when both dates are supplied.
    // Bound the time window itself so a busy station cannot create a giant response.
    for(let offset=0;offset<10;offset++){
      let url=`https://api.weather.gov/stations/${config.station}/observations?start=${encodeURIComponent(iso(start+offset*day))}&end=${encodeURIComponent(iso(start+(offset+1)*day))}&limit=100`;
      for(let page=0;url;page++){
        if(page>=10)throw new Error('Weather pagination exceeds bounded collection; no partial baseline');
        const data=await get(url);if(!Array.isArray(data.features))throw new Error('Missing NWS observations');
        for(const f of data.features){const p=f.properties,t=Date.parse(p?.timestamp),v=p?.temperature;
          if(t>=start&&t<end&&finite(v?.value)&&v.unitCode==='wmoUnit:degC')observations.set(iso(t),{timestamp:iso(t),temperature:v.value});}
        const next=data.pagination?.next;
        if(next){
          const nextUrl=new URL(next);if(nextUrl.origin!=='https://api.weather.gov')throw new Error('Unexpected NWS pagination origin');
          // NWS next links can omit both the original date filters and page limit.
          nextUrl.searchParams.set('start',iso(start+offset*day));nextUrl.searchParams.set('end',iso(start+(offset+1)*day));nextUrl.searchParams.set('limit','100');url=nextUrl.href;
        }else url='';
      }
    }
    const groups=new Map<string,{hours:Set<string>;max:number}>();
    for(const o of observations.values()){const d=o.timestamp.slice(0,10),g=groups.get(d)??{hours:new Set<string>(),max:-Infinity};g.hours.add(o.timestamp.slice(11,13));g.max=Math.max(g.max,o.temperature);groups.set(d,g);}
    const days=[...groups].filter(([,g])=>g.hours.size>=18).map(([d,g])=>({date:d,max_c:g.max,hours:g.hours.size})).sort((a,b)=>a.date.localeCompare(b.date));
    if(days.length<5)throw new Error('Fewer than five days have observations in 18 distinct UTC hours; refuse a sparse baseline');
    const successes=days.filter(d=>d.max_c>=config.threshold_c).length;
    question={id:`nws-${config.station}-${config.target_date}-${config.threshold_c}`,question:`Will reported temperature at ${config.station} reach ${config.threshold_c} °C on ${config.target_date} UTC?`,resolution_date:config.target_date,
      dated_metric:`Maximum valid temperature.value in wmoUnit:degC among NWS API observations at ${config.station} with timestamp in [${config.target_date}T00:00:00Z, ${iso(Date.parse(config.target_date)+day)}). Capture at least 24 hours after the window ends. Require observations in at least 18 distinct UTC hours; otherwise leave unresolved. Settle on that saved snapshot, without later revisions. YES iff maximum >= ${config.threshold_c} °C.`,
      event_type:'quantity_threshold',conditions:['reported station observations, not grid forecasts','at least 18 distinct observed UTC hours'],numeric_clause:{threshold:config.threshold_c,threshold_dir:'>=',ci_unit:'°C'},
      baseline:{probability:jeffreys(successes,days.length),description:`Jeffreys-smoothed exceedance frequency: (${successes} + 0.5) / (${days.length} + 1) from eligible days in the previous 10 UTC days. A short-window comparator, not an NWS probability forecast.`}};
    summary={station:config.station,observation_window:{start:iso(start),end:iso(end)},eligible_days:days,baseline:question.baseline};
    metadata={source:'NOAA National Weather Service',terms:'https://www.weather.gov/documentation/services-web-api',time_semantics:'Observation timestamp is measurement time, not publication time. The API does not provide a first-publication timestamp for each row. captured_at records this retrieval only.',limitations:['Short recent climatology ignores seasonality and forecast dynamics.','Incomplete days are excluded explicitly. This is a reported-temperature event, not certified daily climatology.','NWS observations may arrive late or be revised. This live snapshot cannot prove historical availability.'],baseline_sample_size:days.length};
  }else if(config.kind==='macro'){
    if(!/^[A-Z0-9]{5,24}$/.test(config.series)||!/^\d{4}-(0[1-9]|1[0-2])$/.test(config.target_month)||!date(config.resolution_date)||config.resolution_date<=today||!finite(config.threshold)||!config.unit?.trim())throw new Error('Macro needs a series, target month, future resolution date, threshold and unit');
    const year=now.getUTCFullYear();
    const url=`https://api.bls.gov/publicAPI/v2/timeseries/data/${config.series}?startyear=${year-2}&endyear=${year}`;
    const data=await get(url),series=data.Results?.series?.find((s:any)=>s.seriesID===config.series);
    if(data.status!=='REQUEST_SUCCEEDED'||!Array.isArray(series?.data))throw new Error('BLS did not return the requested series');
    const values=series.data.filter((r:any)=>/^M(0[1-9]|1[0-2])$/.test(r.period)).map((r:any)=>({month:`${r.year}-${r.period.slice(1)}`,value:Number(String(r.value).replace(/,/g,'')),footnotes:r.footnotes})).sort((a:any,b:any)=>a.month.localeCompare(b.month));
    if(!values.length||values.at(-1).month>=config.target_month)throw new Error('Target month is already present in the current BLS snapshot');
    if(config.target_month+'-01'>config.resolution_date)throw new Error('Resolution date predates the target month');
    const window=values.slice(-24),missing=window.filter((r:any)=>!finite(r.value));
    const training=window.filter((r:any)=>finite(r.value));if(training.length<12)throw new Error('BLS baseline needs at least 12 prior monthly observations');
    const successes=training.filter((r:any)=>r.value>=config.threshold).length;
    question={id:`bls-${config.series}-${config.target_month}-${config.resolution_date}`,question:`Will BLS series ${config.series} for ${config.target_month} be at least ${config.threshold} ${config.unit} in the ${config.resolution_date} snapshot?`,resolution_date:config.resolution_date,
      dated_metric:`BLS public API series ${config.series}, year ${config.target_month.slice(0,4)}, period M${config.target_month.slice(5)}. Use a saved API snapshot captured during ${config.resolution_date} UTC. YES iff published value >= ${config.threshold} ${config.unit}. If that period is absent or no snapshot was captured that day, leave unresolved. Later revisions are excluded; this is the chosen snapshot vintage, not a claim about the first release.`,
      event_type:'quantity_threshold',conditions:['specified API snapshot vintage','missing target period remains unresolved'],numeric_clause:{threshold:config.threshold,threshold_dir:'>=',ci_unit:config.unit},
      baseline:{probability:jeffreys(successes,training.length),description:`Jeffreys-smoothed exceedance frequency: (${successes} + 0.5) / (${training.length} + 1) using the last ${training.length} monthly values in the current vintage. No first-release or seasonal independence claim.`}};
    summary={series:config.series,current_vintage_months:training,missing_months:missing.map((r:any)=>({month:r.month,footnotes:r.footnotes})),baseline:question.baseline};
    metadata={source:'US Bureau of Labor Statistics',terms:'https://www.bls.gov/bls/linksite.htm',time_semantics:'year/period describe the reference month. They are never treated as publication dates. The API returns the current vintage; captured_at is the observation of that vintage.',limitations:['Current-vintage history contains revisions and cannot substitute for a historical release archive.','Confirm the chosen series unit and release schedule before issuing.','The simple frequency baseline ignores serial dependence and structural change.'],baseline_sample_size:training.length};
  }else if(config.kind==='world'){
    const start=Date.parse(config.start),end=Date.parse(config.end);
    if(!finite(config.minimum_magnitude)||config.minimum_magnitude<4||!Number.isFinite(start)||!Number.isFinite(end)||start<=now.getTime()||end<=start||end-start>31*day)throw new Error('World pack needs a future window up to 31 days and magnitude >=4');
    const historyEnd=now.getTime(),historyStart=historyEnd-90*day;
    const url=`https://earthquake.usgs.gov/fdsnws/event/1/query?format=geojson&starttime=${encodeURIComponent(iso(historyStart))}&endtime=${encodeURIComponent(iso(historyEnd))}&minmagnitude=${config.minimum_magnitude}&eventtype=earthquake&limit=20000&orderby=time-asc`;
    const data=await get(url);if(!Array.isArray(data.features)||data.features.length>=20000||(data.metadata?.count!==undefined&&data.metadata.count!==data.features.length))throw new Error('USGS catalog incomplete or malformed');
    const seen=new Set<string>();const events=data.features.map((f:any)=>{
      if(!f.id||seen.has(f.id)||!finite(f.properties?.time)||!finite(f.properties?.mag)||f.properties.time<historyStart||f.properties.time>historyEnd||f.properties.mag<config.minimum_magnitude)throw new Error('Invalid/duplicate USGS event');seen.add(f.id);
      return {id:f.id,occurred_at:iso(f.properties.time),updated_at:finite(f.properties.updated)?iso(f.properties.updated):null,magnitude:f.properties.mag};});
    const probability=1-Math.exp(-events.length/90*((end-start)/day));
    question={id:`usgs-m${config.minimum_magnitude}-${iso(start)}-${iso(end)}`,question:`Will USGS record at least one magnitude ${config.minimum_magnitude}+ earthquake worldwide between ${iso(start)} and ${iso(end)}?`,resolution_date:iso(end).slice(0,10),
      dated_metric:`USGS earthquake catalog, eventtype=earthquake, origin time >= ${iso(start)} and < ${iso(end)}, magnitude >= ${config.minimum_magnitude}, worldwide. Capture a catalog snapshot between 7 and 8 days after the window ends. YES iff at least one matching event is present. If the snapshot is missing/incomplete, leave unresolved. Freeze that catalog vintage; subsequent revisions do not change the outcome.`,event_type:'occurrence',conditions:['worldwide','specified origin-time window and catalog vintage'],
      baseline:{probability,description:`Homogeneous Poisson baseline: 1 - exp(-${events.length}/90 × ${(end-start)/day} days), from the preceding 90-day current catalog.`}};
    summary={history_start:iso(historyStart),history_end:iso(historyEnd),events,baseline:question.baseline};
    metadata={source:'US Geological Survey earthquake catalog',terms:'https://www.usgs.gov/information-policies-and-instructions/copyrights-and-credits',time_semantics:'properties.time is event origin time; properties.updated is the latest catalog update, not first publication. Neither proves when a historical forecaster could have known the event.',limitations:['Natural world-event starter; this does not model geopolitical events.','Poisson independence is a deliberately simple baseline; earthquakes cluster.','Magnitudes and catalog coverage can change. No earthquake warning capability is claimed.'],baseline_sample_size:events.length};
  }else throw new Error('Unknown data pack');
  validateQuestion(question);
  const text=JSON.stringify({source:metadata.source,time_semantics:metadata.time_semantics,limitations:metadata.limitations,summary},null,2);
  const last=snapshots.at(-1)!;
  return {schema_version:1,config,question,metadata,snapshots,evidence:packet([{id:config.kind,url:snapshots[0].url,title:metadata.source+' live snapshot',text,fetched_at:last.captured_at,published_at:null,publication_basis:'unknown',kind:'structured',content_sha256:sha256(text),snapshot_sha256:sha256(snapshots.map(s=>s.sha256).join('\n'))}])};
}
