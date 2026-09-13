import { readFile, writeFile } from 'node:fs/promises'
import openapiTS, { astToString } from 'openapi-typescript'

const args = process.argv.slice(2)
if (args.length !== 1 || !['--check', '--write'].includes(args[0])) {
  throw new Error('Use --check or --write.')
}
const source = new URL('../../api/openapi.json', import.meta.url)
const destination = new URL('../lib/api.generated.ts', import.meta.url)
const schema = JSON.parse(await readFile(source, 'utf8'))
const ast = await openapiTS(schema, { alphabetize: true, defaultNonNullable: false })
const output = '// Generated from apps/api/openapi.json. Do not edit. Run pnpm api:generate.\n'
  + astToString(ast)

if (args[0] === '--write') {
  await writeFile(destination, output)
  console.log('Updated lib/api.generated.ts.')
} else {
  const current = await readFile(destination, 'utf8').catch((error) => {
    if (error.code === 'ENOENT') return ''
    throw error
  })
  if (current !== output) {
    console.error('Generated API types are stale. Run pnpm api:generate and commit the result.')
    process.exitCode = 1
  } else {
    console.log('Generated API types match the OpenAPI snapshot.')
  }
}
