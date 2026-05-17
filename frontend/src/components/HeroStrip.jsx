import { MDASH, fmtMaybeNumber } from '../fmt'
import styles from './HeroStrip.module.css'

function RegimeBadge({ regime, cacheReady, feedErr }) {

  if (feedErr || !cacheReady) {
    return <span className={`${styles.pill} ${styles.pillMuted}`}>PENDING</span>
  }



  const r = regime || 'UNKNOWN'

  

  if (r === 'MEAN_REVERTING') return <span className={`${styles.pill} ${styles.pillBlue}`}>{r}</span>



  if (r === 'VOLATILITY_SHOCK') return <span className={`${styles.pill} ${styles.pillOrange}`}>{r}</span>



  if (r === 'NO_TRADE') return <span className={`${styles.pill} ${styles.pillGrey}`}>{r}</span>



  return <span className={`${styles.pill} ${styles.pillMuted}`}>PENDING</span>


}



export default function HeroStrip({ status }) {


  const cacheReady = Boolean(status?.cache_ready)




  const feedErr = Boolean(status?.data_feed_error)




  

  let nifty = MDASH

  

  let vix = MDASH

  

  

  if (cacheReady && !feedErr) {






    nifty = fmtMaybeNumber(status?.nifty_price, 2)





    vix = fmtMaybeNumber(status?.vix_price, 2)





  

  }



  const hurst =
    cacheReady && !feedErr
      ? `H(Idx): ${fmtMaybeNumber(status?.h_idx, 2)} | H(VIX): ${fmtMaybeNumber(status?.h_vix, 2)}`
      : MDASH

  const marketClosed = Boolean(status?.market_closed)
  const sub = marketClosed ? 'Market Closed' : MDASH

  return (
    <section className={styles.hero}>
      {feedErr ? <div className={styles.feedError}>{status.data_feed_error}</div> : null}
      {!cacheReady && !feedErr ? <div className={styles.banner}>WARMING UP...</div> : null}
      {cacheReady && marketClosed ? (
        <div className={styles.marketClosedBanner}>Market Closed</div>
      ) : null}

      <div className={styles.grid}>
        <div className={styles.col}>
          <div className={styles.label}>Nifty 50</div>
          <div className={styles.value}>{feedErr ? MDASH : nifty}</div>
          <div className={styles.sub}>{marketClosed ? 'Market Closed' : sub}</div>
        </div>
        <div className={styles.col}>
          <div className={styles.label}>India VIX</div>
          <div className={styles.value}>{feedErr ? MDASH : vix}</div>
          <div className={styles.sub}>{marketClosed ? 'Market Closed' : sub}</div>
        </div>
        <div className={styles.col}>
          <div className={styles.label}>Regime</div>
          <div className={styles.regimeWrap}>
            <RegimeBadge regime={status?.regime} cacheReady={cacheReady} feedErr={feedErr} />
          </div>
          <div className={styles.sub}>{marketClosed ? 'Market Closed' : sub}</div>
        </div>
        <div className={styles.col}>
          <div className={styles.label}>Hurst Values</div>
          <div className={styles.value}>{feedErr ? MDASH : hurst}</div>
          <div className={styles.sub}>{sub}</div>
        </div>
      </div>
    </section>
  )
}

