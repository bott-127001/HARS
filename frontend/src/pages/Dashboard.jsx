import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { authFetch, clearToken, getToken } from '../api'
import Navbar from '../components/Navbar.jsx'
import HeroStrip from '../components/HeroStrip.jsx'
import LiveScanTable from '../components/LiveScanTable.jsx'
import TradeHistoryTable from '../components/TradeHistoryTable.jsx'

import styles from './Dashboard.module.css'

export default function Dashboard(){
  const nav=useNavigate();
  const [tab,setTab]=useState('scan');
  const [status,setStatus]=useState(null);

  useEffect(()=>{
    if(!getToken()){
      nav('/login');
    }
  },[nav]);

  useEffect(()=>{
    let cancelled=false;
    async function tick(){
      try{
        const s=await authFetch('/api/status');
        if(!cancelled) setStatus(s);
      }catch{
        if(!cancelled) setStatus(null);
      }
    }
    void tick();
    const id=setInterval(()=>void tick(),10_000);
    return ()=>{cancelled=true;clearInterval(id);};
  },[]);

  const title=useMemo(()=>tab==='scan'?'Live Scan':'Trade History',[tab]);

  function logout(){
    clearToken();
    nav('/login');
  }

  return (
    <div className={styles.page}>
      <Navbar onLogout={logout}/>
      <HeroStrip status={status}/>
      <div className={styles.tabsWrap}>
        <div className={styles.tabs}>
          <button type="button" className={tab==='scan'?styles.tabActive:styles.tab} onClick={()=>setTab('scan')}>Live Scan</button>
          <button type="button" className={tab==='hist'?styles.tabActive:styles.tab} onClick={()=>setTab('hist')}>Trade History</button>
        </div>
        <div className={styles.tabTitle}>{title}</div>
      </div>
      <main className={styles.main}>
        {tab==='scan'?<LiveScanTable/>:<TradeHistoryTable/>}
      </main>
    </div>
  );
}
