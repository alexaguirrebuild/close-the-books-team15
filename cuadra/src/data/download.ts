const REVOKE_AFTER_MS = 10_000

/** The URL has to outlive the click: some browsers start the download a moment later. */
export function downloadBytes(bytes: Uint8Array, name: string, type = 'text/csv'): void {
  const url = URL.createObjectURL(new Blob([bytes as BlobPart], { type }))
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = name
  anchor.click()
  setTimeout(() => URL.revokeObjectURL(url), REVOKE_AFTER_MS)
}
