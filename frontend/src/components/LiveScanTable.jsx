import { useEffect, useState } from 'react'

import { authFetch } from '../api'
import { isMarketHoursIST } from '../market'
import { MDASH, fmtMaybeNumber } from '../fmt'

import styles from './Tables.module.css'

const INFO =
  'Pre-market data fetch at 8:45 AM IST. Gap data at 9:18 AM IST. First scan at 9:20:15 AM IST.'

export default function LiveScanTable() {
  const [rows, setRows] = useState([])

  async function load() {
    try {
      const data = await authFetch('/api/scan')
      setRows(Array.isArray(data) ? data : [])
    } catch {
      setRows([])
    }
  }

  useEffect(() => {
    void load()
    const mk = isMarketHoursIST()
    const ms = mk ? 30_000 : 300_000
    const id = setInterval(() => void load(), ms)
    return () => clearInterval(id)
  }, [])

  return (
    <div>
      <div className={styles.infoBar}>{INFO}</div>
      <table className={styles.table}>
        <thead>
          <tr>
            <th>Symbol</th>
            <th>RVOL</th>
            <th>ATR%</th>
            <th>Gap%</th>
            <th>Momentum 15m</th>
            <th>Result</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, idx) => {
            const zebra = idx % 2 === 1 ? styles.rowAlt : ''
            const rvol = fmtMaybeNumber(r.rvol, 2)
            const atr = fmtMaybeNumber(r.atr_pct, 2)
            const gp = fmtMaybeNumber(r.gap_pct, 2)
            const mom = fmtMaybeNumber(r.momentum_15m, 2)

            const result = String(r.result || '')
            let resCls = styles.resGrey
            let resTxt = MDASH

            if (result === 'SIGNAL') {
              resCls = styles.resSignal
              resTxt = 'SIGNAL'
            } else if (result === 'WATCH') {
              resCls = styles.resWatch
              resTxt = 'WATCH'
            }

            return (
              <tr key={`${r.symbol}-${idx}`} className={zebra}>
                <td>{r.symbol}</td>
                <td>{rvol}</td>
                <td>{atr}</td>
                <td>{gp}</td>
                <td>{mom}</td>
                <td className={resCls}>{resTxt}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
