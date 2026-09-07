# 前端 Dockerfile - 多阶段构建
FROM node:20-alpine AS builder

# 国内镜像源（可通过 --build-arg 覆盖）
ARG NPM_REGISTRY=https://registry.npmmirror.com

WORKDIR /app

# 确保安装 devDependencies（vite 等构建工具）
ENV NODE_ENV=development

# 配置 npm 国内镜像
RUN npm config set registry ${NPM_REGISTRY}

# 复制依赖文件
COPY package.json package-lock.json* ./
RUN npm install --include=dev

# 复制源代码并构建
COPY . .
RUN npm run build

# 生产阶段 - 使用 nginx
FROM nginx:alpine

# 复制构建产物
COPY --from=builder /app/dist /usr/share/nginx/html

# 复制 nginx 配置
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
