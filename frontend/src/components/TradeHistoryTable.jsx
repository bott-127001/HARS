import { useEffect, useState } from 'react'
import { authFetch } from '../api'

import styles from './Tables.module.css'
import { MDASH } from '../fmt'

function fmtPrice(v){
  if (v===null || v===undefined) return MDASH;
  const n=Number(v);
  if(Number.isNaN(n)) return MDASH;
  return n.toFixed(2);
}

function StatusCell({ s }) {
  const t = String(s || '')
  if (t === 'WIN') return <span className={styles.resWin}>{t}</span>
  if (t === 'LOSS') return <span className={styles.resLoss}>{t}</span>

  return <span className={styles.resGrey}>{t}</span>
}

export default function TradeHistoryTable(){
  const [rows,setRows]=useState([]);

  async function load(){
    try{
      const data=await authFetch('/api/history');
      setRows(Array.isArray(data)?data:[]);
    }catch{
      setRows([]);
    }
  }

  useEffect(()=>{
    void load();
    const id=setInterval(()=>void load(),60_000);
    return ()=>clearInterval(id);
  },[]);

  return (
    <div>
      <table className={styles.table}>
        <thead>
          <tr>
            <th>Date</th>
            <th>Stock</th>
            <th>Direction</th>
            <th>Entry</th>
            <th>TP</th>
            <th>SL</th>
            <th>Regime</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r,idx)=>{
            const zebra=idx%2===1?styles.rowAlt:'';
            return (
              <tr key={`${r.date}-${r.symbol}-${idx}`} className={zebra}>
                <td>{r.date||MDASH}</td>
                <td>{r.symbol||MDASH}</td>
                <td>{r.direction||MDASH}</td>
                <td>{fmtPrice(r.entry)}</td>
                <td>{fmtPrice(r.tp)}</td>
                <td>{fmtPrice(r.sl)}</td>
                <td>{r.regime||MDASH}</td>
                <td><StatusCell s={r.status}/></td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
