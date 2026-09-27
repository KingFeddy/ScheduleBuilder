import { test, expect } from '@playwright/test'
import { formatCourseTitle, formatTopicTitle } from '../lib/course-metadata'

test('cleans Banner title casing and punctuation without damaging acronyms or existing names', () => {
  const examples = [
    ['ST: PHYSICAL AI.', 'ST: Physical AI'],
    ['PHYSICS III - HONORS', 'Physics III - Honors'],
    ['INTRO TO GENAI - HONORS', 'Intro To GenAI - Honors'],
    ['MATERIAL FUNDAMENTALS OF BME - HONORS', 'Material Fundamentals Of BME - Honors'],
    ['Introduction to Computer Science II in C++. ', 'Introduction to Computer Science II in C++'],
    ['MONSTERS: HUMANITY&#39;S HIDDEN FACE', "Monsters: Humanity's Hidden Face"],
    ['iOS and C++', 'iOS and C++'],
    ['St: Physical Ai', 'ST: Physical AI'],
    ['Co-Op Work Experience Iii', 'Co-Op Work Experience III'],
  ]
  for (const [source, expected] of examples) {
    expect(formatCourseTitle(source)).toBe(expected)
    expect(formatTopicTitle(source)).toBe(expected)
    expect(formatCourseTitle(expected)).toBe(expected)
  }
  expect(formatCourseTitle(null)).toBeNull()
  expect(formatCourseTitle(undefined)).toBeUndefined()
})
