import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { getToken, loginRequest, setToken } from '../api'

import styles from './Login.module.css'

export default function Login(){
  const nav=useNavigate();
  const [authResolved,setAuthResolved]=useState(false);
  const [u,setU]=useState('');
  const [p,setP]=useState('');
  const [err,setErr]=useState('');

  function hasValidToken() {
    return Boolean(getToken());
  }

  useEffect(()=>{
    if (hasValidToken()) {
      nav('/dashboard', { replace: true });
      return;
    }
    setAuthResolved(true);
  },[nav]);

  async function onSubmit(e){
    e.preventDefault();
    setErr('');
    try{
      const res=await loginRequest(u,p);
      setToken(res.token);
      nav('/dashboard');
    }catch{
      setErr('Invalid credentials');
    }
  }

  if (!authResolved) {
    return null;
  }

  return (
    <div className={styles.page}>
      <form className={styles.card} onSubmit={onSubmit}>
        <div className={styles.title}>Nifty Signal Login</div>
        {err? <div className={styles.error}>{err}</div>:null}
        <label className={styles.label}>
          <div className={styles.labelText}>Username</div>
          <input className={styles.input} value={u} onChange={(e)=>setU(e.target.value)} autoComplete="username" />
        </label>
        <label className={styles.label}>
          <div className={styles.labelText}>Password</div>
          <input className={styles.input} type="password" value={p} onChange={(e)=>setP(e.target.value)} autoComplete="current-password" />
        </label>
        <button className={styles.btn} type="submit">Login</button>
      </form>
    </div>
  );
}
