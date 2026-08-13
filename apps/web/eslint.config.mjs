import nextVitals from "eslint-config-next/core-web-vitals";
import nextTypescript from "eslint-config-next/typescript";

const config = [
  { ignores: [".next/**", "node_modules/**", "next-env.d.ts"] },
  ...nextVitals,
  ...nextTypescript,
  {
    // The application intentionally loads API state from effects. Migrating all
    // existing screens to a server/data-cache architecture is a separate change.
    rules: { "react-hooks/set-state-in-effect": "off" },
  },
];
export default config;
