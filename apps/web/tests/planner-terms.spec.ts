import { test, expect } from '@playwright/test'
import { planningDefault, planningTermOptions } from '../lib/planner-terms'

test('uses server defaults and offers future terms without collected sections', () => {
  expect(planningDefault('202650')).toBe('202690')
  expect(planningDefault('202510')).toBe('202510')
  for (const value of ['2026950', '2026', null, '000090']) expect(planningDefault(value)).toBeNull()
  const options = planningTermOptions('202690', '202010')
  expect(options).toContain('203190')
  expect(options).toContain('202010')
  expect(options.some((term) => term.endsWith('50'))).toBe(false)
})
