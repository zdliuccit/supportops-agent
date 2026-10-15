ARG NODE_IMAGE
ARG NGINX_IMAGE
FROM ${NODE_IMAGE} AS build
WORKDIR /app
RUN corepack enable && corepack prepare pnpm@11.10.0 --activate
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/web_chat/package.json apps/web_chat/package.json
RUN pnpm install --frozen-lockfile && pnpm rebuild esbuild
COPY apps/web_chat/src apps/web_chat/src
COPY apps/web_chat/public apps/web_chat/public
COPY apps/web_chat/index.html apps/web_chat/tsconfig.json apps/web_chat/vite.config.ts apps/web_chat/
# 浏览器使用同源 /v1；服务端秘密不进入 Vite 构建参数。
ENV VITE_API_BASE_URL=""
RUN pnpm run web:build
FROM ${NGINX_IMAGE} AS web
COPY --from=build /app/apps/web_chat/dist /usr/share/nginx/html
COPY deploy/runtime/nginx.conf /etc/nginx/nginx.conf
COPY deploy/runtime/site.conf /etc/nginx/conf.d/default.conf
USER 101:101
EXPOSE 8080
STOPSIGNAL SIGQUIT
ENTRYPOINT ["nginx"]
CMD ["-g", "daemon off;"]
