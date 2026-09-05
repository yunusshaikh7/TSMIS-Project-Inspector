/* Explicit browser preview only; this file is excluded from the desktop package. */
(() => {
  const base = 'C:\\Users\\Sample\\Documents\\ArcGIS';
  const second = base + '\\TSNR';
  const project = (root, name, environment, version, status, extra = []) => {
    const folder = 'TSMIS';
    const rows = version ? [{environment,version}, ...extra].map(r => ({...r,layer:'State Highway Network',map:'Highway overview',kind:'Layer',versions:[r.version],folder,service:'lrs_tsmis_' + r.environment.toLowerCase(),dataset:'0',is_tsmis:true,status:'Version found',version_kind:'Service version',url:`https://gis-${r.environment.toLowerCase()}.example.org/server/rest/services/${folder}/lrs_tsmis_${r.environment.toLowerCase()}/FeatureServer`,environment_evidence:'Environment inferred from server name'})) : [{layer:'Local boundaries',map:'Map',kind:'Layer',is_tsmis:false,environment:'Unknown',status:'No inspectable connection'}];
    return {name, path:root + '\\' + name.replace('.aprx','') + '\\' + name,
      versions:version ? rows.map(r=>r.version) : [], environments:version ? [...new Set(rows.map(r=>r.environment))] : [],
      folders:version ? [folder] : [], services:version ? [...new Set(rows.map(r=>r.service))] : [],
      status, tsmis_connections:version ? rows.length : 0, errors:[], rows};
  };
  const projects = root => root === second ? [project(root,'TSNR Pilot.aprx','Dev','editor.TSNR_Pilot','Identified')] : [project(root,'District 07 Review.aprx','Prod','sde.DEFAULT','Identified'),project(root,'Highway Inventory.aprx','Prod','editor.September_Update','Identified'),project(root,'Intersection QA.aprx','Test','qa.Intersection_Review','Identified'),project(root,'TSNR Planning.aprx','Prod','sde.DEFAULT','Mixed connections',[{environment:'Dev',version:'editor.TSNR_Pilot'}]),project(root,'Reference Maps.aprx','','','No TSMIS connections')];
  const empty = () => ({running:false,message:'Ready',total:0,completed:0,projects:[],warnings:[],diagnostic_scan:false,error:'',has_result:false,complete:false,last_refreshed:null});
  const key = 'tsmis-saved-lists-preview-v3';
  let stored;
  try { stored = JSON.parse(localStorage.getItem(key)); } catch {}
  let preferences = stored?.preferences || {root:base,python:'C:\\Program Files\\ArcGIS\\Pro\\bin\\Python\\envs\\arcgispro-py3\\python.exe',recursive:true,match:'tsmis'};
  const finished = root => ({...empty(),root,projects:projects(root),completed:projects(root).length,total:projects(root).length,complete:true,has_result:true,message:'Saved list',last_refreshed:'2026-09-04T17:30:00Z'});
  let saved = stored?.saved || {[base]:finished(base),[second]:finished(second)};
  let result = structuredClone(saved[preferences.root] || empty());
  const paths = () => Object.entries(saved).map(([root,r])=>({root,refreshed_at:r.last_refreshed,projects:r.projects.length}));
  const persist = () => localStorage.setItem(key,JSON.stringify({preferences,saved}));
  persist();
  window.demoApi = {
    get_initial_state: async () => ({version:'0.4.0',settings:preferences,arcgis_found:true,state:result,saved_paths:paths()}),
    choose_folder: async () => preferences.root === base ? second : base, choose_python: async () => preferences.python,
    save_settings: async values => { preferences = values; persist(); return {ok:true}; },
    select_path: async root => { preferences.root=root; result=structuredClone(saved[root] || empty()); persist(); return {ok:true,settings:preferences,state:result,saved_paths:paths()}; },
    get_saved_paths: async () => paths(),
    clear_list: async root => { delete saved[root]; result=empty(); persist(); return {ok:true,state:result,saved_paths:paths()}; },
    start_scan: async (values, diagnostics) => { preferences=values; result={...empty(),root:values.root,running:true,has_result:true,last_refreshed:saved[values.root]?.last_refreshed || null,message:'Reading sample projects…',total:projects(values.root).length,diagnostic_scan:diagnostics}; return {ok:true}; },
    get_scan_state: async () => {
      if(result.running) {
        const source=projects(result.root), n=result.projects.length;
        if(n<source.length) result.projects.push(source[n]);
        result.completed=result.projects.length;
        if(result.completed===source.length) {result.running=false;result.complete=true;result.message='Saved list';result.last_refreshed=new Date().toISOString();saved[result.root]=structuredClone(result);persist();}
      }
      return structuredClone(result);
    },
    get_project: async index => result.projects[index], stop_scan: async () => {result.running=false;result.message='Refresh stopped';},
    save_results: async () => ({ok:false,cancelled:true}),
    check_updates: async () => ({ok:true,available:false,message:'Updates are available in the desktop app.'})
  };
})();
