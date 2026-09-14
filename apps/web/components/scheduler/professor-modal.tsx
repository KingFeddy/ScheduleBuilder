'use client'

import { useEffect, useState } from 'react'
import { X, Star, Flame, ArrowUpRight } from 'lucide-react'
import { getApiErrorMessage, getProfessor, type ProfessorResponse } from '@/lib/api'
import { useSchedulerStore } from '@/store/scheduler'
import { VibeCheckPill } from '@/components/ui/vibe-check-pill'

interface ProfessorModalProps {
  professorName: string
  onClose: () => void
}

function formatName(raw: string): string {
  if (!raw.includes(',')) return raw
  const [last, first = ''] = raw.split(', ')
  return `${first} ${last}`.trim()
}

// Spec §5 RmpBadge thresholds — single source of truth. professor-picker.tsx
// imports these rather than keeping its own copy, so the picker dropdown and
// this modal can never show a different color for the same rating.
export function ratingColor(score: number): string {
  if (score >= 4.0) return 'text-green'
  if (score >= 3.0) return 'text-yellow'
  return 'text-njit-red'
}

export function difficultyColor(score: number): string {
  if (score <= 2.5) return 'text-green'
  if (score <= 3.9) return 'text-yellow'
  return 'text-njit-red'
}

export function ProfessorModal({ professorName, onClose }: ProfessorModalProps) {
  const professorCache = useSchedulerStore((s) => s.professorCache)

  // Initialize from cache synchronously — eliminates the loading flash when
  // the modal opens after a solve (prefetch already populated the cache).
  const [data, setData] = useState<ProfessorResponse | null | 'loading'>(() =>
    professorName in professorCache ? professorCache[professorName] : 'loading',
  )
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (professorName in professorCache) return
    getProfessor(professorName).then(setData).catch((err) => {
      const message = getApiErrorMessage(err, 'Failed to load professor ratings. Close and reopen to try again.')
      if (message) {
        setError(message)
        setData(null)
      }
    })
  }, [professorName, professorCache])

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  const loading = data === 'loading'
  const prof = loading ? null : data

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: 'rgba(0,0,0,0.7)' }}
      onClick={onClose}
    >
      <div
        className="relative w-full max-w-lg rounded-xl border border-border bg-surface overflow-y-auto max-h-[85vh]"
        onClick={(e) => e.stopPropagation()}
      >
        <button
          onClick={onClose}
          className="absolute top-4 right-4 text-faint hover:text-text transition-colors duration-150"
        >
          <X className="w-4 h-4" />
        </button>

        <div className="p-6 space-y-5">
          {/* Header */}
          <div>
            {loading ? (
              <div className="h-3 w-8 rounded bg-surface-2 animate-pulse mb-2" />
            ) : (
              <p className="text-xs font-medium uppercase tracking-wider text-muted mb-1">
                {prof?.department ?? 'Unknown'}
              </p>
            )}
            <p className="text-xl font-semibold tracking-tight">{formatName(professorName)}</p>
          </div>

          {error && <p role="alert" className="text-sm text-yellow">{error}</p>}
          {!loading && !error && data === null && (
            <p className="text-sm text-muted">No ratings found for this professor.</p>
          )}

          {/* Ratings */}
          <div className="flex gap-8">
            <div>
              <p className="text-xs font-medium uppercase tracking-wider text-muted mb-1">Rating</p>
              {loading ? (
                <div className="h-6 w-20 rounded bg-surface-2 animate-pulse" />
              ) : (
                <p className={`flex items-center gap-1.5 font-mono text-xl ${ratingColor(prof?.rmp_score ?? 0)}`}>
                  <Star className="w-5 h-5 flex-shrink-0" />
                  {prof?.rmp_score != null ? (
                    <>
                      {prof.rmp_score.toFixed(1)}
                      <span className="text-muted text-sm"> / 5.0</span>
                    </>
                  ) : (
                    'N/A'
                  )}
                </p>
              )}
            </div>
            <div>
              <p className="text-xs font-medium uppercase tracking-wider text-muted mb-1">
                Difficulty
              </p>
              {loading ? (
                <div className="h-6 w-20 rounded bg-surface-2 animate-pulse" />
              ) : (
                <p className={`flex items-center gap-1.5 font-mono text-xl ${difficultyColor(prof?.rmp_difficulty ?? 0)}`}>
                  <Flame className="w-5 h-5 flex-shrink-0" />
                  {prof?.rmp_difficulty != null ? (
                    <>
                      {prof.rmp_difficulty.toFixed(1)}
                      <span className="text-muted text-sm"> / 5.0</span>
                    </>
                  ) : (
                    'N/A'
                  )}
                </p>
              )}
            </div>
          </div>

          {/* Meta */}
          {!loading && (prof?.rmp_would_take_again != null || prof?.rmp_num_ratings != null) && (
            <p className="text-xs text-muted">
              {prof.rmp_would_take_again != null && (
                <span>Would take again: {Math.round(prof.rmp_would_take_again)}%&nbsp;&nbsp;</span>
              )}
              {prof.rmp_num_ratings != null && (
                <span>Based on {prof.rmp_num_ratings} ratings</span>
              )}
            </p>
          )}

          {/* Tags */}
          {!loading && prof?.rmp_tags && prof.rmp_tags.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {prof.rmp_tags.map((tag) => (
                <VibeCheckPill key={tag} tag={tag} />
              ))}
            </div>
          )}

          {/* RMP link */}
          <a
            href={`https://www.ratemyprofessors.com/search/professors?q=${encodeURIComponent(formatName(professorName))}`}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 text-xs text-muted underline underline-offset-2 hover:text-text transition-colors duration-150"
          >
            View on RateMyProfessors
            <ArrowUpRight className="w-3 h-3" />
          </a>
        </div>
      </div>
    </div>
  )
}
