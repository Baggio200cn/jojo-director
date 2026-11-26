import { useState } from 'react'
import { useI18n } from './I18n'

export default function BrandFilm() {
  const { t } = useI18n()
  const [playing, setPlaying] = useState(false)
  const base = import.meta.env.BASE_URL.replace(/\/$/, '')
  const poster = `${base}/brand-evolution-poster.png`
  return <div className="brand-film">
    <div className="brand-film-media">
      {playing ? <video src={`${base}/brand-evolution.mp4`} poster={poster} controls autoPlay muted playsInline
        onEnded={() => setPlaying(false)} onError={() => setPlaying(false)} />
        : <>
          <img src={poster} alt={t('JOJO Director 最终品牌标识')} />
          <button className="brand-film-play" onClick={() => setPlaying(true)} aria-label={t('播放品牌演变过程')}>
            <span>▶</span> {t('播放品牌演变过程')}
          </button>
        </>}
    </div>
    <div className="brand-film-caption">
      <b>{t('从一条轨道，到科学场景生产引擎')}</b>
      <span>{t('约 30 秒；点击播放，结束后回到最终标识。')}</span>
    </div>
  </div>
}
