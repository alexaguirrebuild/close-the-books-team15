#!/usr/bin/env node
// Extracts the embedded text of the PDFs of a phase, invoices and remittance advices, into a CSV.
// No OCR: pages without a text layer are listed so they can be handled one by one.
// Usage: npm run pdf:index -- /path/to/live [--out /path/to/out/live/pdfs_text.csv]
// Use --out with the real data: the phase folders stay as they were delivered.
import { createWriteStream, existsSync, readdirSync, readFileSync, statSync, writeFileSync } from 'node:fs'
import { join, relative } from 'node:path'
import { getDocument } from 'pdfjs-dist/legacy/build/pdf.mjs'

const folder = process.argv[2]
if (!folder) {
  process.stderr.write('Uso: npm run pdf:index -- /ruta/a/la/carpeta [--out fichero.csv]\n')
  process.exit(1)
}
const outFlag = process.argv.indexOf('--out')
const outPath = outFlag > 0 ? process.argv[outFlag + 1] : join(folder, 'pdfs_text.csv')
const PDF_FOLDERS = ['pdfs', 'remittance_advices']

const walk = (dir) =>
  readdirSync(dir).flatMap((name) => {
    const path = join(dir, name)
    if (statSync(path).isDirectory()) return walk(path)
    return name.toLowerCase().endsWith('.pdf') ? [path] : []
  })

const csv = (value) => `"${String(value).replace(/"/g, '""')}"`

const pdfs = PDF_FOLDERS.flatMap((name) => (existsSync(join(folder, name)) ? walk(join(folder, name)) : []))
if (!pdfs.length) {
  process.stderr.write(`No hay PDF en ${folder}\n`)
  process.exit(1)
}

const output = createWriteStream(outPath)
output.write('file,page,text\n')
const withoutText = []
const failed = []
let pages = 0

for (const [index, path] of pdfs.entries()) {
  const file = relative(folder, path)
  try {
    const task = getDocument({
      data: new Uint8Array(readFileSync(path)),
      useSystemFonts: true,
      verbosity: 0,
    })
    const document = await task.promise
    for (let number = 1; number <= document.numPages; number += 1) {
      const page = await document.getPage(number)
      const content = await page.getTextContent()
      const text = content.items.map((item) => item.str ?? '').join(' ').replace(/\s+/g, ' ').trim()
      if (text) {
        output.write(`${csv(file)},${number},${csv(text)}\n`)
        pages += 1
      } else {
        withoutText.push(`${file}#${number}`)
      }
    }
    await task.destroy()
  } catch (error) {
    failed.push(`${file}: ${error instanceof Error ? error.message : String(error)}`)
  }
  if ((index + 1) % 500 === 0) process.stdout.write(`${index + 1}/${pdfs.length} PDF\n`)
}

output.end()
if (withoutText.length) writeFileSync(`${outPath}.sin_texto.txt`, `${withoutText.join('\n')}\n`)
process.stdout.write(
  `${pdfs.length} PDF, ${pages} páginas con texto, ${withoutText.length} sin capa de texto, ${failed.length} con error\n`,
)
if (failed.length) process.stderr.write(`${failed.slice(0, 20).join('\n')}\n`)
