FROM node:22-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
ARG QUANTX_API_URL=http://backend:8000
ENV QUANTX_API_URL=$QUANTX_API_URL
RUN npm run build

FROM node:22-slim
WORKDIR /app
ENV NODE_ENV=production QUANTX_API_URL=http://backend:8000
COPY --from=build /app ./
EXPOSE 3000
CMD ["npx", "next", "start", "-p", "3000", "-H", "0.0.0.0"]
