import React,{useState} from 'react';
import type {FilterOptions} from '../../shared/contracts';
import {ScopeControls} from './ScopeControls';
import {Dashboard} from './Dashboard';
import {Logs} from './Logs';
import {OcrPage} from './OcrPage';
import {emptyScope,type Scope,type OpenMessages} from './model';
import './insights.css';
export function InsightsPage({mode,options,openMessages}:{mode:'dashboard'|'logs'|'ocr';options:FilterOptions;openMessages:OpenMessages}){
 const [scope,setScope]=useState(emptyScope);const [logResult,setLogResult]=useState<string|null>(null);
 const [dashboardScope,setDashboardScope]=useState<Scope|null>(null);
 function showLogs(result:string){setDashboardScope(scope);setLogResult(result);setScope({...scope,from:"",to:""});}
 const active=logResult===null?mode:'logs';
 return <section className="insights-page"><ScopeControls value={scope} options={options} allowDeleted={active!=='logs'} onChange={setScope}/>
 {logResult!==null&&<button onClick={()=>{setLogResult(null);if(dashboardScope)setScope(dashboardScope);setDashboardScope(null);}}>返回统计仪表盘</button>}
 {active==='dashboard'&&<Dashboard scope={scope} openMessages={openMessages} openLogs={showLogs}/>}
 {active==='logs'&&<Logs key={JSON.stringify([scope,logResult])} scope={scope} initialResult={logResult??''} messageDates={dashboardScope??undefined} openMessages={openMessages}/>}
 {active==='ocr'&&<OcrPage key={JSON.stringify(scope)} scope={scope} openMessages={openMessages}/>}
 </section>;
}
