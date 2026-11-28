import { useState } from 'react'
import { useI18n } from './I18n'

export default function BrandFilm() {
  const { t } = useI18n()
  const [playing, setPlaying] = useState(false)
  const base = import.meta.env.BASE_URL.replace(/\/$/, '')
  const poster = `${base}/brand-evolution-poster.png`
  return <div className="brand-film">
    <div className="brand-film-media">
      {playing ? <video src={`${base}/brand-evolution-music.mp4`} poster={poster} controls autoPlay playsInline
        onEnded={() => setPlaying(false)} onError={() => setPlaying(false)} />
        : <button className="brand-film-poster" onClick={() => setPlaying(true)} aria-label={t('播放品牌演变过程')}>
          <img src={poster} alt={t('JOJO Director 最终品牌标识')} />
          <span className="brand-film-play" aria-hidden="true">▶</span>
        </button>}
    </div>
    <div className="brand-film-caption">
      <b>{t('从一条轨道，到科学场景生产引擎')}</b>
      <span>{t('约 30 秒；点击播放，结束后回到最终标识。')}</span>
    </div>
  </div>
}
