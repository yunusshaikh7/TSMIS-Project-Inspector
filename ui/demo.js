/* Only used by the explicit #demo browser preview; never substituted for a scan. */
(() => {
  const project = (name, environment, version, folder, status, extra = []) => ({
    name, path: 'C:\\Users\\Sample\\Documents\\ArcGIS\\' + name.replace('.aprx','') + '\\' + name,
    versions: version ? [version,...extra.map(x => x.version)] : [], environments: [...new Set(environment ? [environment,...extra.map(x=>x.environment)] : [])],
    folders: folder ? [folder] : [], status, tsmis_connections: version ? 1 + extra.length : 0, errors: [],
    rows: version ? [{layer:'State Highway Network',map:'Highway overview',kind:'Layer',environment,version,versions:[version],folder,service:'lrs_tsmis',dataset:'0',is_tsmis:true,status:'Version found',version_kind:'Service version',url:`https://gis-${environment.toLowerCase()}.example.org/server/rest/services/${folder}/lrs_tsmis/FeatureServer`,environment_evidence:'Environment inferred from server name'},...extra.map(r=>({...r,layer:'Project intersections',map:'Highway overview',kind:'Layer',is_tsmis:true,folder,service:'lrs_tsmis',status:'Version found'}))] : [{layer:'Local boundaries',map:'Map',kind:'Layer',is_tsmis:false,environment:'Unknown',status:'No inspectable connection'}]
  });
  const projects = [project('District 07 Review.aprx','Prod','sde.DEFAULT','TSMIS','Identified'),project('Highway Inventory.aprx','Prod','sample.September_Update','TSMIS','Identified'),project('Intersection QA.aprx','Test','qa.Intersection_Review','TSMIS_QA','Identified'),project('TSNR Planning.aprx','Prod','sde.DEFAULT','TSMIS','Mixed connections',[{environment:'Dev',version:'editor.TSNR_Pilot'}]),project('Reference Maps.aprx','','','','No TSMIS connections')];
  let result = {running:false,message:'Preview ready',total:0,completed:0,projects:[],warnings:[],diagnostic_scan:false,error:''};
  let preferences = {root:'C:\\Users\\Sample\\Documents\\ArcGIS',python:'C:\\Program Files\\ArcGIS\\Pro\\bin\\Python\\envs\\arcgispro-py3\\python.exe',recursive:true,match:'tsmis'};
  window.demoApi = {
    get_initial_state: async () => ({version:'0.2.0',settings:preferences,arcgis_found:true}),
    choose_folder: async () => preferences.root, choose_python: async () => preferences.python,
    save_settings: async values => { preferences = values; return {ok:true}; },
    start_scan: async (values, diagnostics) => { result = {running:true,message:'Reading sample projects…',total:5,completed:0,projects:[],warnings:[],diagnostic_scan:diagnostics,error:''}; return {ok:true}; },
    get_scan_state: async () => { if(result.running) { const n = result.projects.length; if(n < projects.length) result.projects.push(projects[n]); result.completed = result.projects.length; if(result.completed === 5) {result.running=false;result.message='Sample scan complete';} } return structuredClone(result); },
    get_project: async index => projects[index], stop_scan: async () => {result.running=false;result.message='Sample scan stopped';},
    save_results: async () => ({ok:false,cancelled:true}),
    check_updates: async () => ({ok:true,available:false,message:'Updates are available in the desktop app.'})
  };
})();
