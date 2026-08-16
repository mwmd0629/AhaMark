"use strict";

(function attachMathInputConverter(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.AhaMarkMathInput = api;
})(typeof globalThis === "object" ? globalThis : this, () => {
  const GREEK = new Map([
    ["α", "\\alpha"],
    ["β", "\\beta"],
    ["γ", "\\gamma"],
    ["δ", "\\delta"],
    ["θ", "\\theta"],
    ["λ", "\\lambda"],
    ["μ", "\\mu"],
    ["π", "\\pi"],
    ["σ", "\\sigma"],
    ["φ", "\\phi"],
    ["ω", "\\omega"],
  ]);
  const RESERVED = new Set(["sqrt", "lim", "int", "sum", "matrix", "cases"]);

  class MathInputError extends Error {
    constructor(message, position) {
      super(`${message}（位置 ${position + 1}）`);
      this.name = "MathInputError";
      this.position = position;
    }
  }

  function normalizeSource(source) {
    return source
      .normalize("NFC")
      .replace(/\u2212/g, "-")
      .replace(/\u2192/g, "->")
      .replace(/\u221a/g, "sqrt")
      .replace(/\u2264/g, "<=")
      .replace(/\u2265/g, ">=");
  }

  function tokenize(source) {
    const normalized = normalizeSource(source);
    const tokens = [];
    let index = 0;
    while (index < normalized.length) {
      const character = normalized[index];
      if (/\s/u.test(character)) {
        index += 1;
        continue;
      }
      if (normalized.startsWith("->", index)) {
        tokens.push({ type: "arrow", value: "->", position: index });
        index += 2;
        continue;
      }
      if (["<=", ">="].includes(normalized.slice(index, index + 2))) {
        tokens.push({
          type: normalized.slice(index, index + 2),
          value: normalized.slice(index, index + 2),
          position: index,
        });
        index += 2;
        continue;
      }
      if (GREEK.has(character) || character === "∞") {
        tokens.push({ type: "symbol", value: character, position: index });
        index += character.length;
        continue;
      }
      const number = normalized.slice(index).match(/^(?:\d+(?:\.\d+)?|\.\d+)/u);
      if (number) {
        tokens.push({ type: "number", value: number[0], position: index });
        index += number[0].length;
        continue;
      }
      const identifier = normalized
        .slice(index)
        .match(/^[A-Za-z]+|^[\p{Script=Han}]+/u);
      if (identifier) {
        tokens.push({
          type: RESERVED.has(identifier[0]) ? "keyword" : "identifier",
          value: identifier[0],
          position: index,
        });
        index += identifier[0].length;
        continue;
      }
      if ("+-*=,<>/^_()[];".includes(character)) {
        tokens.push({ type: character, value: character, position: index });
        index += 1;
        continue;
      }
      throw new MathInputError(`不支持字符“${character}”`, index);
    }
    tokens.push({ type: "eof", value: "", position: normalized.length });
    return tokens;
  }

  class Parser {
    constructor(source) {
      this.source = source;
      this.tokens = tokenize(source);
      this.index = 0;
    }

    current() {
      return this.tokens[this.index];
    }

    match(type) {
      if (this.current().type !== type) return null;
      const token = this.current();
      this.index += 1;
      return token;
    }

    require(type, message) {
      const token = this.match(type);
      if (!token) throw new MathInputError(message, this.current().position);
      return token;
    }

    parse() {
      if (this.current().type === "eof") {
        throw new MathInputError("请输入公式", 0);
      }
      const node = this.parseRelation(new Set(["eof"]));
      if (this.current().type !== "eof") {
        throw new MathInputError(
          "这里存在未识别或有歧义的结构",
          this.current().position,
        );
      }
      return node;
    }

    parseRelation(stop) {
      let node = this.parseAdd(stop);
      while (
        !stop.has(this.current().type) &&
        ["=", "<", ">", "<=", ">="].includes(this.current().type)
      ) {
        const operator = this.current().type;
        this.index += 1;
        node = {
          type: "binary",
          operator,
          left: node,
          right: this.parseAdd(stop),
        };
      }
      return node;
    }

    parseAdd(stop) {
      let node = this.parseProduct(stop);
      while (
        !stop.has(this.current().type) &&
        ["+", "-"].includes(this.current().type)
      ) {
        const operator = this.current().type;
        this.index += 1;
        node = {
          type: "binary",
          operator,
          left: node,
          right: this.parseProduct(stop),
        };
      }
      return node;
    }

    parseProduct(stop) {
      let node = this.parseFractionTerm(stop);
      while (!stop.has(this.current().type)) {
        if (this.match("*")) {
          node = {
            type: "binary",
            operator: "*",
            left: node,
            right: this.parseFractionTerm(stop),
          };
          continue;
        }
        if (this.startsPrimary(this.current())) {
          node = {
            type: "product",
            left: node,
            right: this.parseFractionTerm(stop),
          };
          continue;
        }
        break;
      }
      return node;
    }

    parseFractionTerm(stop) {
      let node = this.parsePostfix(stop);
      if (!this.match("/")) return node;
      if (node.type !== "group") {
        throw new MathInputError(
          "分子必须用 (...) 或 [...] 明确括起",
          this.tokens[this.index - 1].position,
        );
      }
      const right = this.parsePostfix(stop);
      if (right.type !== "group") {
        throw new MathInputError(
          "分母必须用 (...) 或 [...] 明确括起",
          this.current().position,
        );
      }
      node = {
        type: "fraction",
        numerator: node.body,
        denominator: right.body,
        numeratorBracket: node.bracket,
        denominatorBracket: right.bracket,
      };
      if (this.current().type === "/") {
        throw new MathInputError(
          "连续分式必须逐层使用括号明确结构",
          this.current().position,
        );
      }
      return node;
    }

    startsPrimary(token) {
      return ["number", "identifier", "symbol", "keyword", "(", "["].includes(
        token.type,
      );
    }

    parsePostfix(stop) {
      let node = this.parsePrimary(stop);
      let subscript = null;
      let superscript = null;
      if (
        ["limit", "largeOperator"].includes(node.type) &&
        ["_", "^"].includes(this.current().type)
      ) {
        throw new MathInputError(
          "极限、积分或求和的界限已在算子语法中给出，不能重复添加",
          this.current().position,
        );
      }
      while (["_", "^"].includes(this.current().type)) {
        const operator = this.current();
        this.index += 1;
        const argument = this.parseScriptArgument(stop);
        if (operator.type === "_") {
          if (subscript)
            throw new MathInputError(
              "同一对象不能重复写下标",
              operator.position,
            );
          subscript = argument;
        } else {
          if (superscript)
            throw new MathInputError(
              "同一对象不能重复写上标",
              operator.position,
            );
          superscript = argument;
        }
      }
      if (subscript || superscript)
        node = { type: "script", base: node, subscript, superscript };
      return node;
    }

    parseScriptArgument(stop) {
      if (["(", "["].includes(this.current().type))
        return this.parsePrimary(stop).body;
      if (["number", "identifier", "symbol"].includes(this.current().type))
        return this.parsePrimary(stop);
      throw new MathInputError(
        "上下标必须是单个值或括号内表达式",
        this.current().position,
      );
    }

    parsePrimary(stop) {
      const token = this.current();
      if (token.type === "+" || token.type === "-") {
        this.index += 1;
        return {
          type: "unary",
          operator: token.type,
          body: this.parsePrimary(stop),
        };
      }
      if (
        token.type === "number" ||
        token.type === "identifier" ||
        token.type === "symbol"
      ) {
        this.index += 1;
        return { type: "atom", value: token.value };
      }
      if (token.type === "(" || token.type === "[") return this.parseGroup();
      if (token.type === "keyword") {
        this.index += 1;
        if (token.value === "sqrt") return this.parseSqrt(token.position);
        if (token.value === "lim") return this.parseLimit(token.position);
        if (token.value === "int" || token.value === "sum")
          return this.parseLargeOperator(token);
        if (token.value === "matrix") return this.parseMatrix(token.position);
        if (token.value === "cases") return this.parseCases(token.position);
      }
      if (token.type === "/") {
        throw new MathInputError(
          "只支持显式的 [分子]/[分母] 或 (分子)/(分母)",
          token.position,
        );
      }
      throw new MathInputError("缺少表达式", token.position);
    }

    parseGroup() {
      const opening = this.current();
      this.index += 1;
      const closing = opening.type === "(" ? ")" : "]";
      if (this.current().type === closing) {
        throw new MathInputError("括号内不能为空", this.current().position);
      }
      const items = [this.parseRelation(new Set([",", closing]))];
      while (this.match(",")) {
        items.push(this.parseRelation(new Set([",", closing])));
      }
      this.require(closing, `缺少配对的 ${closing}`);
      const body = items.length === 1 ? items[0] : { type: "sequence", items };
      return { type: "group", bracket: opening.type, body };
    }

    parseSqrt(position) {
      if (this.current().type !== "(") {
        throw new MathInputError(
          "sqrt 后必须使用 (...) 明确根号范围",
          position,
        );
      }
      return { type: "sqrt", body: this.parseGroup().body };
    }

    parseLimit(position) {
      if (
        !this.startsPrimary(this.current()) ||
        this.current().type === "keyword"
      ) {
        throw new MathInputError("lim 后必须写趋近变量", position);
      }
      const from = this.parsePrimary(new Set(["arrow"]));
      this.require("arrow", "lim 必须使用 -> 或 → 指明趋近值");
      if (
        !this.startsPrimary(this.current()) ||
        this.current().type === "keyword"
      ) {
        throw new MathInputError("lim 缺少趋近值", this.current().position);
      }
      const to = this.parsePrimary(new Set());
      return { type: "limit", from, to };
    }

    parseLargeOperator(token) {
      this.require(
        "_",
        `${token.value} 必须显式填写下限，如 ${token.value}_a^b`,
      );
      const lower = this.parseScriptArgument(new Set());
      let upper = null;
      if (this.match("^")) upper = this.parseScriptArgument(new Set());
      return { type: "largeOperator", operator: token.value, lower, upper };
    }

    parseRows(keyword, position) {
      this.require("(", `${keyword} 后必须使用 (...)`);
      const rows = [];
      while (true) {
        this.require("[", `${keyword} 的每一行必须写成 [...]`);
        const cells = [];
        while (true) {
          cells.push(this.parseRelation(new Set([",", "]"])));
          if (!this.match(",")) break;
        }
        this.require("]", `${keyword} 行缺少 ]`);
        rows.push(cells);
        if (!this.match(";")) break;
      }
      this.require(")", `${keyword} 缺少配对的 )`);
      if (!rows.length)
        throw new MathInputError(`${keyword} 不能为空`, position);
      return rows;
    }

    parseMatrix(position) {
      const rows = this.parseRows("matrix", position);
      const width = rows[0].length;
      if (!width || rows.some((row) => row.length !== width)) {
        throw new MathInputError("matrix 每一行必须有相同列数", position);
      }
      return { type: "matrix", rows };
    }

    parseCases(position) {
      const rows = this.parseRows("cases", position);
      if (rows.some((row) => row.length !== 2)) {
        throw new MathInputError(
          "cases 每一行必须恰好包含表达式和条件两项",
          position,
        );
      }
      return { type: "cases", rows };
    }
  }

  function renderLatex(node) {
    switch (node.type) {
      case "atom":
        if (GREEK.has(node.value)) return GREEK.get(node.value);
        if (node.value === "∞") return "\\infty";
        return node.value;
      case "group": {
        const [left, right] = node.bracket === "(" ? ["(", ")"] : ["[", "]"];
        return `${left}${renderLatex(node.body)}${right}`;
      }
      case "binary":
        return `${renderLatex(node.left)}${node.operator === "*" ? "\\cdot " : node.operator === "<=" ? "\\le " : node.operator === ">=" ? "\\ge " : node.operator}${renderLatex(node.right)}`;
      case "unary":
        return `${node.operator}${renderLatex(node.body)}`;
      case "sequence":
        return node.items.map(renderLatex).join(",");
      case "product":
        return `${renderLatex(node.left)}${renderLatex(node.right)}`;
      case "fraction":
        return `\\frac{${renderLatex(node.numerator)}}{${renderLatex(node.denominator)}}`;
      case "sqrt":
        return `\\sqrt{${renderLatex(node.body)}}`;
      case "script":
        return `${renderLatex(node.base)}${node.subscript ? `_{${renderLatex(node.subscript)}}` : ""}${node.superscript ? `^{${renderLatex(node.superscript)}}` : ""}`;
      case "limit":
        return `\\lim_{${renderLatex(node.from)}\\to${renderLatex(node.to)}}`;
      case "largeOperator":
        return `\\${node.operator}_{${renderLatex(node.lower)}}${node.upper ? `^{${renderLatex(node.upper)}}` : ""}`;
      case "matrix":
        return `\\begin{bmatrix}${node.rows.map((row) => row.map(renderLatex).join(" & ")).join(" \\\\ ")}\\end{bmatrix}`;
      case "cases":
        return `\\begin{cases}${node.rows.map((row) => `${renderLatex(row[0])} & ${renderLatex(row[1])}`).join(" \\\\ ")}\\end{cases}`;
      default:
        throw new Error("未知公式节点");
    }
  }

  function renderLinear(node) {
    switch (node.type) {
      case "atom":
        return node.value;
      case "group": {
        const right = node.bracket === "(" ? ")" : "]";
        return `${node.bracket}${renderLinear(node.body)}${right}`;
      }
      case "binary":
        return `${renderLinear(node.left)}${node.operator === "-" ? "−" : node.operator === "<=" ? "≤" : node.operator === ">=" ? "≥" : node.operator}${renderLinear(node.right)}`;
      case "unary":
        return `${node.operator === "-" ? "−" : "+"}${renderLinear(node.body)}`;
      case "sequence":
        return node.items.map(renderLinear).join(",");
      case "product":
        return `${renderLinear(node.left)}${["limit", "largeOperator"].includes(node.left.type) ? " " : ""}${renderLinear(node.right)}`;
      case "fraction":
        return `${node.numeratorBracket}${renderLinear(node.numerator)}${node.numeratorBracket === "(" ? ")" : "]"}/${node.denominatorBracket}${renderLinear(node.denominator)}${node.denominatorBracket === "(" ? ")" : "]"}`;
      case "sqrt":
        return `√(${renderLinear(node.body)})`;
      case "script":
        return `${renderLinear(node.base)}${node.subscript ? `_{${renderLinear(node.subscript)}}` : ""}${node.superscript ? `^(${renderLinear(node.superscript)})` : ""}`;
      case "limit":
        return `lim_{${renderLinear(node.from)}→${renderLinear(node.to)}}`;
      case "largeOperator":
        return `${node.operator === "int" ? "∫" : "Σ"}_{${renderLinear(node.lower)}}${node.upper ? `^(${renderLinear(node.upper)})` : ""}`;
      case "matrix":
        return `matrix(${node.rows.map((row) => `[${row.map(renderLinear).join(", ")}]`).join("; ")})`;
      case "cases":
        return `cases(${node.rows.map((row) => `[${row.map(renderLinear).join(", ")}]`).join("; ")})`;
      default:
        throw new Error("未知公式节点");
    }
  }

  function convert(source) {
    if (typeof source !== "string") throw new TypeError("普通输入必须是字符串");
    const ast = new Parser(source).parse();
    return { ast, latex: renderLatex(ast), linearText: renderLinear(ast) };
  }

  return { MathInputError, convert };
});
