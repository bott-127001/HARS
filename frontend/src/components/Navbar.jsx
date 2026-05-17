import { useEffect, useState } from 'react'
import styles from './Navbar.module.css'

function getISTString() {
  const now = new Date()
  const time = new Intl.DateTimeFormat('en-IN', {
    timeZone: 'Asia/Kolkata',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: true,
  }).format(now)
  return `${time} IST`
}

export default function Navbar({ onLogout }) {
  const [clock, setClock] = useState(getISTString())
  useEffect(() => {
    const id = setInterval(() => setClock(getISTString()), 1000)
    return () => clearInterval(id)
  }, [])

  return (
    <header className={styles.navbar}>
      <div className={styles.title}>Nifty Signal</div>
      <div className={styles.clock}>{clock}</div>
      <button type="button" className={styles.logout} onClick={onLogout}>
        Logout
      </button>
    </header>
  )

}
