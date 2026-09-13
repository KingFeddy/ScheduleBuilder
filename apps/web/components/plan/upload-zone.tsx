'use client'

import { useState, useRef, type DragEvent, type ChangeEvent } from 'react'
import { UploadCloud } from 'lucide-react'
import { getApiErrorMessage, parsePlan, type ParsedDegreeValidated } from '@/lib/api'

interface UploadZoneProps {
  onParsed: (parsed: ParsedDegreeValidated) => void
}

async function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve((reader.result as string).split(',')[1])
    reader.onerror = reject
    reader.readAsDataURL(file)
  })
}

async function fileToHash(file: File): Promise<string> {
  const buffer = await file.arrayBuffer()
  const hashBuffer = await crypto.subtle.digest('SHA-256', buffer)
  return Array.from(new Uint8Array(hashBuffer))
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('')
}

export function UploadZone({ onParsed }: UploadZoneProps) {
  const [isDragging, setIsDragging] = useState(false)
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  async function processFile(file: File) {
    setError(null)

    if (!file.name.toLowerCase().endsWith('.pdf') && file.type !== 'application/pdf') {
      setError('Please upload a PDF file.')
      return
    }
    if (file.size > 5 * 1024 * 1024) {
      setError('File too large — maximum size is 5MB.')
      return
    }
    if (file.size < 5 * 1024) {
      setError("File too small — this doesn't look like a valid DegreeWorks PDF.")
      return
    }

    setIsLoading(true)
    try {
      const [pdfBase64, clientPdfHash] = await Promise.all([
        fileToBase64(file),
        fileToHash(file),
      ])
      const res = await parsePlan(pdfBase64, clientPdfHash)
      try {
        localStorage.setItem('njit-dw-parsed', JSON.stringify(res.parsed))
        localStorage.setItem('njit-dw-hash', res.server_hash)
      } catch { /* Safari private mode */ }
      onParsed(res.parsed)
    } catch (err) {
      setError(getApiErrorMessage(err, 'Failed to parse PDF. Please try again.'))
    } finally {
      setIsLoading(false)
    }
  }

  function onDragOver(e: DragEvent<HTMLDivElement>) {
    e.preventDefault()
    setIsDragging(true)
  }

  function onDragLeave(e: DragEvent<HTMLDivElement>) {
    e.preventDefault()
    setIsDragging(false)
  }

  function onDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault()
    setIsDragging(false)
    const file = e.dataTransfer.files[0]
    if (file) processFile(file)
  }

  function onFileChange(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (file) processFile(file)
    e.target.value = ''
  }

  return (
    <div className="flex flex-col items-center gap-3">
      <div
        onClick={() => !isLoading && inputRef.current?.click()}
        onDragOver={onDragOver}
        onDragLeave={onDragLeave}
        onDrop={onDrop}
        className={[
          'w-full max-w-lg rounded-xl border-2 border-dashed p-10 flex flex-col items-center gap-3 transition-colors duration-150',
          isLoading ? 'cursor-default' : 'cursor-pointer',
          isDragging
            ? 'border-njit-red bg-red-dim'
            : 'border-border hover:border-border-strong',
        ].join(' ')}
      >
        {isLoading ? (
          <>
            <div className="w-10 h-10 rounded-full bg-surface-2 animate-pulse" />
            <div className="h-3 rounded bg-surface-2 animate-pulse w-48" />
            <p className="text-sm text-muted">Reading your degree audit…</p>
          </>
        ) : (
          <>
            <UploadCloud className="w-10 h-10 text-muted" />
            <div className="flex flex-col items-center gap-1 text-center">
              <p className="text-sm font-medium text-text">Upload your DegreeWorks PDF</p>
              <p className="text-xs text-muted">Drag and drop or click to browse</p>
            </div>
            <p className="text-xs text-faint text-center">
              Export from DegreeWorks → Actions → Print/Save as PDF
            </p>
          </>
        )}
      </div>

      {error && <p className="text-sm text-njit-red">{error}</p>}

      <input
        ref={inputRef}
        type="file"
        accept=".pdf,application/pdf"
        className="hidden"
        onChange={onFileChange}
      />
    </div>
  )
}
