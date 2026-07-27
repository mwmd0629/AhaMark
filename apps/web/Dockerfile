FROM node:22-alpine AS deps
WORKDIR /app
COPY package*.json ./
COPY apps/web/package.json apps/web/package.json
RUN npm ci && npm install --no-save \
    @next/swc-linux-x64-musl@15.5.7 \
    lightningcss-linux-x64-musl@1.32.0 \
    @tailwindcss/oxide-linux-x64-musl@4.3.3
FROM deps AS build
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
ENV NEXT_IGNORE_INCORRECT_LOCKFILE=1
ENV NEXT_TELEMETRY_DISABLED=1
COPY apps/web apps/web
RUN npm run build --workspace apps/web
FROM node:22-alpine
WORKDIR /app
ENV NODE_ENV=production
ENV HOSTNAME=0.0.0.0
COPY --from=build /app/apps/web/.next/standalone ./
COPY --from=build /app/apps/web/.next/static ./apps/web/.next/static
EXPOSE 3000
CMD ["node","apps/web/server.js"]
