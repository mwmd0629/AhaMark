"use strict";

const assert = require("node:assert/strict");
const {
  MathInputError,
  convert,
} = require("./recognition_math_input_to_latex.js");

const cases = [
  ["[a]/[b]", "\\frac{a}{b}"],
  ["(a)/(b)", "\\frac{a}{b}"],
  ["sqrt(x+1)", "\\sqrt{x+1}"],
  ["x^2+x_(i+1)", "x^{2}+x_{i+1}"],
  ["lim x->0", "\\lim_{x\\to0}"],
  [
    "lim (x,y)->(0,0) [sqrt(xy+1)-1]/(x+y)",
    "\\lim_{(x,y)\\to(0,0)}\\frac{\\sqrt{xy+1}-1}{x+y}",
  ],
  ["int_a^b sqrt(x)", "\\int_{a}^{b}\\sqrt{x}"],
  ["∫_a^b x", "\\int_{a}^{b}x"],
  ["iint_D f", "\\iint_{D}f"],
  ["∫∫_D f", "\\iint_{D}f"],
  ["∬_D f", "\\iint_{D}f"],
  ["sum_(i=1)^n [1]/(i^2)", "\\sum_{i=1}^{n}\\frac{1}{i^{2}}"],
  [
    "[sqrt([a]/[b])+x^2]/(y_(i+1))",
    "\\frac{\\sqrt{\\frac{a}{b}}+x^{2}}{y_{i+1}}",
  ],
  ["matrix([a,b];[c,d])", "\\begin{bmatrix}a & b \\\\ c & d\\end{bmatrix}"],
  [
    "cases([x^2,x>=0];[-x,x<0])",
    "\\begin{cases}x^{2} & x\\ge 0 \\\\ -x & x<0\\end{cases}",
  ],
  ["√(α+β)−∞", "\\sqrt{\\alpha+\\beta}-\\infty"],
];

for (const [source, expected] of cases) {
  assert.equal(convert(source).latex, expected, source);
}

assert.equal(
  convert("lim (x,y)->(0,0) [sqrt(xy+1)-1]/(x+y)").linearText,
  "lim_{(x,y)→(0,0)} [√(xy+1)−1]/(x+y)",
);

for (const source of [
  "a/b",
  "[a]/b",
  "a/[b]",
  "[a]/[b]/[c]",
  "sqrt x",
  "lim x 0",
  "lim ->0",
  "int^b",
  "iint^b",
  "int_a^b^c",
  "lim x->0_1",
  "x^",
  "matrix([a,b];[c])",
  "cases([x,x>0];[0])",
  "(a+b",
  "x@2",
]) {
  assert.throws(() => convert(source), MathInputError, source);
}

process.stdout.write(
  `${JSON.stringify({ status: "passed", cases: cases.length, ambiguity_rejections: 16, network_used: false, private_data_used: false })}\n`,
);
