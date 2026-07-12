# 服务器运维规范

> 适用架构：Caddy + 多项目分散式 Docker Compose
> 所有业务服务均运行在 Docker 容器中，通过共享内部网络互通，Caddy 作为唯一对外入口（支持容器化和宿主机两种部署方式，见 2.1 节）。

---

## 一、Workspace 目录结构

> workspace 统一位于 `/srv/workspace/`，符合 Linux FHS 规范，不得放置在其他路径（如 `/root/` 或根目录下自建目录）。
>
> **注意：此 `/srv/workspace` 是服务器项目的统一工作目录，与 Agent 自身的 workspace（比如 Kiro/Hermes 工具内部的工作区概念）是两个完全不同的东西，不要混淆。**

```
/srv/workspace/
├── infra/                        # 基础设施（所有服务器必配）
│   ├── caddy/                    # 反向代理 + TLS
│   │   ├── docker-compose.yml
│   │   ├── Caddyfile
│   │   ├── data/                 # 证书持久化
│   │   └── config/
│   ├── postgres/                 # 主数据库（优先使用）
│   │   ├── docker-compose.yml
│   │   └── .env
│   ├── mariadb/                  # 备用数据库（特殊项目使用）
│   │   ├── docker-compose.yml
│   │   └── .env
│   └── redis/                    # 缓存服务
│       └── docker-compose.yml
│
├── websites/                     # 前端项目部署配置（含页面的 Web 应用）
│   └── project_A/
│       ├── docker-compose.yml
│       └── .env
│
├── services/                     # 后端服务部署配置（纯 API / 后台服务）
│   └── project_C/
│       ├── docker-compose.yml
│       └── .env
│
├── code/                         # 所有项目源码（开发目录）
│   ├── project_A/                # 独立项目：单一源码目录
│   └── project_E/                # 关联项目（前后端同属一个产品）
│       ├── frontend/
│       └── backend/
│
└── docs/                         # 运维文档
    ├── 运维相关/
    ├── 项目文档/
    └── 经验沉淀/
```

> **源码目录规范：**
> - 所有项目源码统一放在 `code/` 下，不散放在 workspace 根目录
> - 部署配置（compose、.env）与源码分离，分别归属 `websites/` 或 `services/`
> - 若一个产品同时包含前端和后端（即使分开部署），源码必须放在 `code/` 下的同一目录中，以子目录区分，不得拆散到两个独立目录
> - compose 的 `build.context` 必须明确指向 `/srv/workspace/code/...` 对应目录，禁止引用历史目录或临时目录

---

## 二、基础设施配置规范

### 2.1 网络拓扑

本规范支持两种 Caddy 部署方案，可根据实际需求选择，但**同一台服务器只能采用其中一种，不得混用**。

---

#### 方案 A：Caddy 容器化（当前默认方案）

Caddy 作为容器运行，加入 `proxy-network`，与其他项目容器处于同一 Docker 内网，通过容器名直接路由，无需各容器暴露宿主机端口。

```
外部请求（HTTP/HTTPS）
        │
        ▼
┌─────────────────────────────────────────────┐
│                  宿主机                      │
│                                             │
│  ┌──────────────────────────────────────┐  │
│  │          proxy-network（内网）        │  │
│  │                                      │  │
│  │  ┌───────────┐                       │  │
│  │  │infra-caddy│──► wordpress-shop:80  │  │
│  │  │  :80/:443 │──► medusa-backend:9000│  │
│  │  └───────────┘──► project-x:PORT    │  │
│  │        ▲                             │  │
│  └────────┼─────────────────────────────┘  │
│           │ 80/443 映射到宿主机              │
└───────────┼─────────────────────────────────┘
            │
       互联网请求
```

特点：
- 项目容器**不需要**暴露宿主机端口，Caddy 通过 Docker 内网直接访问
- 多个容器可以监听相同的内部端口（如都用 `:80`），互不冲突
- Caddy 根据**域名（Host header）**决定转发目标，而非端口
- 管理方式统一，全部 `docker compose` 管理

限制：
- 宿主机上直接运行的进程（非容器）无法通过容器名访问，需使用 `host.docker.internal`（见混合场景处理）

---

#### 方案 B：Caddy 宿主机化

Caddy 直接安装并运行在宿主机（systemd 管理），不作为容器。

```
外部请求（HTTP/HTTPS）
        │
        ▼
┌─────────────────────────────────────────────┐
│                  宿主机                      │
│                                             │
│  Caddy（systemd）监听 :80 / :443            │
│    │                                        │
│    ├──► 127.0.0.1:6001                      │
│    ├──► 127.0.0.1:6002                      │
│    └──► 127.0.0.1:6003                      │
│              │                              │
│  ┌───────────┼──────────────────────────┐  │
│  │  proxy-network（容器内网）            │  │
│  │  ┌──────────────┐  ┌──────────────┐  │  │
│  │  │    容器 A    │  │    容器 B    │  │  │
│  │  │ ports:       │  │ ports:       │  │  │
│  │  │ 127.0.0.1:   │  │ 127.0.0.1:  │  │  │
│  │  │ 6001:80      │  │ 6002:80     │  │  │
│  │  └──────────────┘  └──────────────┘  │  │
│  └───────────────────────────────────────┘  │
└─────────────────────────────────────────────┘
```

特点：
- Caddy 直接在宿主机网络中运行，反代宿主机进程时直接用 `127.0.0.1:port`，无需任何绕路
- 对宿主机进程（如 hermes dashboard）反代最简单

限制：
- 每个需要被 Caddy 反代的容器，**必须**将端口映射到宿主机 `127.0.0.1:PORT`
- 宿主机端口不能冲突，需维护端口分配表
- 多个容器不能映射到同一个宿主机端口

---

#### 两种方案对比

| | 方案 A（Caddy 容器化） | 方案 B（Caddy 宿主机化） |
|---|---|---|
| 容器是否需要暴露端口 | 否 | 是（映射到 127.0.0.1） |
| 反代容器服务 | 容器名:端口，直接 | 127.0.0.1:宿主机端口，需维护端口表 |
| 反代宿主机进程 | 需用 `host.docker.internal`，有额外配置 | 直接 `127.0.0.1:port`，最简单 |
| 管理工具统一性 | 全部 docker compose | Caddy 用 systemd，其余用 compose |
| 端口冲突风险 | 无（Docker 内网隔离） | 有，需人工维护分配表 |
| 适合场景 | 服务全部容器化 | 宿主机进程较多 |

---

#### 混合场景处理（方案 A 下存在宿主机进程）

当采用方案 A，但部分服务运行在宿主机（非容器）时，属于混合场景，必须额外处理，否则必然出现 502。

**根因**：Caddy 容器内的 `127.0.0.1` 指向的是容器自身，不是宿主机。

1. Caddy compose 中**必须**配置 `extra_hosts`（方案 A 的标准模板已包含）：

```yaml
extra_hosts:
  - "host.docker.internal:host-gateway"
```

2. Caddyfile 中该服务的 upstream 使用 `host.docker.internal:PORT`，**禁止写 `127.0.0.1:PORT`**：

```caddy
# 错误写法（容器内 127.0.0.1 是 Caddy 自己）
h1.example.com {
    reverse_proxy 127.0.0.1:9119
}

# 正确写法
h1.example.com {
    reverse_proxy host.docker.internal:9119
}
```

3. 排查顺序（按此顺序逐步确认，不要跳步）：

```bash
# 1) 宿主机进程是否在监听
ss -ltnp | grep ':9119'

# 2) 宿主机直连是否正常
curl -I http://127.0.0.1:9119

# 3) Caddy 容器内能否访问宿主机端口
docker exec infra-caddy wget -qO- --timeout=5 http://host.docker.internal:9119

# 4) 再测域名
curl -I https://h1.example.com
```

4. 若第 3 步超时，检查宿主机防火墙是否拦截了来自 Docker 网桥的流量：

```bash
# 确认 ufw 状态（应为 inactive）
ufw status
# 确认 iptables INPUT 默认策略（应为 ACCEPT）
iptables -L INPUT -n | head -3
```

---

#### 基础网络初始化

所有容器通过统一的外部 Docker 网络互通，服务器初始化时创建一次：

```bash
docker network create proxy-network
```

每个 compose 文件中声明加入该网络：

```yaml
networks:
  proxy-network:
    external: true
```

- 防火墙：云服务器统一不启用 ufw，依赖云厂商安全组管控入站流量
- 基础设施（数据库、缓存）不对外暴露，外部访问使用 SSH 隧道连接

### 2.2 Docker 全局日志轮转

写入 `/etc/docker/daemon.json`，防止日志占满磁盘：

```json
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "100m",
    "max-file": "3"
  }
}
```

生效：

```bash
systemctl restart docker
```

### 2.3 Caddy

Caddy 是所有域名的统一入口，负责 TLS 证书自动申请与反向代理。

```yaml
# infra/caddy/docker-compose.yml
services:
  caddy:
    image: caddy:latest
    container_name: infra-caddy
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
    extra_hosts:
      - "host.docker.internal:host-gateway"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile
      - ./data:/data
      - ./config:/config
    networks:
      - proxy-network

networks:
  proxy-network:
    external: true
```

> **`extra_hosts: host.docker.internal:host-gateway` 是必须配置项，不得省略。**
> 凡是需要从 Caddy 容器内反代到宿主机上运行的服务（如 hermes dashboard），必须通过此配置才能正确解析宿主机地址，详见 2.3.1 节。

Caddyfile 配置示例：

```
project-a.example.com {
    reverse_proxy project_a-web:3000
}

project-b.example.com {
    reverse_proxy project_b-web:8080
}
```

> Caddy 对有域名的站点会自动申请 TLS 证书并将 HTTP 重定向到 HTTPS。
> 但在某些环境下（如存在其他默认站点拦截 80 端口），需要显式声明 HTTP 跳转规则：

```
project-a.example.com {
    reverse_proxy project_a-web:3000
}

http://project-a.example.com {
    redir https://project-a.example.com{uri} permanent
}
```

#### 2.3.1 宿主机服务经 Caddy 容器反代

适用于方案 A 下的混合场景。完整说明见 2.1 节「混合场景处理」，核心要点：

- Caddyfile 中宿主机服务的 upstream 必须写 `host.docker.internal:PORT`
- Caddy compose 必须包含 `extra_hosts: host.docker.internal:host-gateway`
- 排查 502 时按 2.1 节的 4 步顺序逐一确认

#### 2.3.2 Caddyfile 宿主机文件与容器视图一致性检查

宿主机编辑的是 `/srv/workspace/infra/caddy/Caddyfile`，Caddy 实际读取的是容器内 `/etc/caddy/Caddyfile`。在容器长期运行或异常重建后，可能出现两者不一致的情况。

标准检查：

```bash
cmp -s /srv/workspace/infra/caddy/Caddyfile \
  <(docker exec infra-caddy cat /etc/caddy/Caddyfile) \
  && echo "same" || echo "different"
```

如果结果为 `different`，不要继续盲目 reload，应直接重建 Caddy 容器：

```bash
cd /srv/workspace/infra/caddy && docker compose up -d --force-recreate
```

重建后再执行：

```bash
docker exec infra-caddy caddy validate --config /etc/caddy/Caddyfile
docker exec infra-caddy caddy reload --config /etc/caddy/Caddyfile
```

#### 2.3.3 域名切换最短排障顺序

新增域名或切换域名时，按下面顺序逐步排查，不要把 DNS、TLS、路由、上游连通混在一起猜：

1. **DNS 是否正确**：`dig +short <domain>`
2. **域名是否进入 Caddy 路由**：`docker exec infra-caddy caddy adapt --config /etc/caddy/Caddyfile --adapter caddyfile`
3. **证书是否成功签发**：`docker logs infra-caddy | grep -E 'obtaining|obtained successfully'`
4. **上游是否可达**：容器服务用 `service_name:port`，宿主机服务用 `host.docker.internal:port`
5. **是否存在旧残留入口**：已停项目的旧域名、旧 upstream、旧容器名是否已清理

只要现象是"证书正常但 502"，优先怀疑上游地址写错；只要"宿主机文件改了但行为没变"，优先怀疑容器视图与宿主机文件不一致（见 2.3.2）。

#### 2.3.4 Caddy 热重载

```bash
docker exec infra-caddy caddy reload --config /etc/caddy/Caddyfile
```

### 2.4 PostgreSQL（主数据库）

> 默认优先使用 PostgreSQL，所有新项目数据库均建议建在此实例上。

```yaml
# infra/postgres/docker-compose.yml
services:
  postgres:
    image: postgres:16
    container_name: infra-postgres
    restart: unless-stopped
    env_file:
      - .env
    volumes:
      - ./data:/var/lib/postgresql/data
      - ./backups:/backups
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U $$POSTGRES_USER -d $$POSTGRES_DB"]
      interval: 10s
      timeout: 5s
      retries: 10
    networks:
      - proxy-network

networks:
  proxy-network:
    external: true
```

`.env` 示例：

```env
POSTGRES_USER=infra_admin
POSTGRES_PASSWORD=<your_password>
POSTGRES_DB=infra
TZ=Asia/Shanghai
```

**必建数据库：`devops`**

初始化后需在 `devops` 数据库中创建以下表：

```sql
-- 凭据与 Token 管理（旧版Interface_pass 的实际实现）
CREATE TABLE credentials (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    value       TEXT NOT NULL,
    platform    TEXT,
    description TEXT,
    purpose     TEXT,
    scope       TEXT,
    expires_at  TIMESTAMPTZ,
    extra       JSONB,
    created_at  TIMESTAMPTZ DEFAULT now(),
    updated_at  TIMESTAMPTZ DEFAULT now()
);
CREATE UNIQUE INDEX idx_credentials_name ON credentials(name);

-- 运维变更审计日志
CREATE TABLE devops_logs (
    id            BIGSERIAL PRIMARY KEY,
    timestamp     TIMESTAMPTZ NOT NULL DEFAULT now(),
    change_type   TEXT NOT NULL,
    project_name  TEXT NOT NULL,
    project_path  TEXT,
    reason        TEXT,
    files_changed TEXT[],
    verification  TEXT,
    operator      TEXT NOT NULL DEFAULT 'hermes-agent',
    status        TEXT NOT NULL DEFAULT 'completed',
    tags          TEXT[]
);
CREATE INDEX idx_devops_logs_timestamp    ON devops_logs(timestamp DESC);
CREATE INDEX idx_devops_logs_project_name ON devops_logs(project_name);
CREATE INDEX idx_devops_logs_change_type  ON devops_logs(change_type);
CREATE INDEX idx_devops_logs_files_changed ON devops_logs USING gin(files_changed);
CREATE INDEX idx_devops_logs_tags         ON devops_logs USING gin(tags);

-- Redis DB 编号登记
CREATE TABLE redis_db_registry (
    db_number    INTEGER PRIMARY KEY,
    project_name TEXT,
    purpose      TEXT,
    status       TEXT NOT NULL DEFAULT 'available',
    note         TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_redis_db_registry_project ON redis_db_registry(project_name);
CREATE INDEX idx_redis_db_registry_status  ON redis_db_registry(status);
```

> - `credentials`：统一存储所有第三方接口、Token 及授权信息，替代旧版 `Interface_pass` 表（字段向上兼容，功能相同，名称已统一）
> - `devops_logs`：所有运维变更的审计记录，替代旧版 `change_log` 表
> - `redis_db_registry`：共享 Redis DB 编号分配登记

### 2.5 MariaDB（备用数据库）

> 仅用于有特殊需求的项目（如依赖 MySQL 生态的应用）。

```yaml
# infra/mariadb/docker-compose.yml
services:
  mariadb:
    image: mariadb:11
    container_name: infra-mariadb
    restart: unless-stopped
    env_file:
      - .env
    command:
      - --character-set-server=utf8mb4
      - --collation-server=utf8mb4_unicode_ci
    volumes:
      - ./data:/var/lib/mysql
      - ./backups:/backups
    healthcheck:
      test: ["CMD-SHELL", "mariadb-admin ping -h 127.0.0.1 -u$$MARIADB_USER -p$$MARIADB_PASSWORD --silent"]
      interval: 10s
      timeout: 5s
      retries: 10
    networks:
      - proxy-network

networks:
  proxy-network:
    external: true
```

`.env` 示例：

```env
MARIADB_USER=infra_admin
MARIADB_PASSWORD=<your_password>
MARIADB_ROOT_PASSWORD=<your_root_password>
MARIADB_DATABASE=infra
TZ=Asia/Shanghai
```

### 2.6 Redis（缓存服务）

> 所有需要缓存、队列、Session 的项目共用此实例。
> 所有接入共享 Redis 的项目必须分配唯一的 Redis DB 编号，并在 `devops.redis_db_registry` 表中登记，确保软隔离、可查、可追溯。

```yaml
# infra/redis/docker-compose.yml
services:
  redis:
    image: redis:7-alpine
    container_name: infra-redis
    restart: unless-stopped
    volumes:
      - ./data:/data
    networks:
      - proxy-network

networks:
  proxy-network:
    external: true
```

#### Redis DB 编号分配规则（强制）

1. 所有接入 `infra-redis` 的项目，必须显式设置：
   - `REDIS_HOST=infra-redis`
   - `REDIS_PORT=6379`
   - `REDIS_DB=<唯一编号>`
2. `REDIS_DB` 禁止多个项目共用，除非用户明确批准
3. 新项目上线前，必须先在 `devops.redis_db_registry` 登记，再写入项目配置；禁止依赖默认 DB 0
4. 项目下线时，必须同步将该 DB 状态更新为 `retired`，不得只删容器不改登记
5. 若项目对隔离要求较高（高价值会话、敏感缓存、重负载任务），应优先评估独立 Redis

#### Redis DB 登记模板

新增项目接入时，执行以下 SQL 登记后再写入项目配置：

```sql
INSERT INTO devops.redis_db_registry (db_number, project_name, purpose, status, note)
VALUES (<db_number>, '<project_name>', '<purpose>', 'in_use', '<备注>');
```

#### Redis DB 分配与回收流程

1. 接入前先查 `redis_db_registry` 确认无冲突
2. 先登记，再写入项目 compose / `.env`
3. 上线后验证：目标 DB 有实际 key 写入，其他 DB 未被误写入
4. 项目下线时确认数据已清理，再将状态改为 `retired`

### 2.7 Beszel Agent（服务器监控）

> 所有服务器均需部署 Beszel Agent，用于系统资源监控（CPU、内存、磁盘、网络、Docker 容器状态）。
> Beszel Hub 部署在 R0（`beszel.local`），各远程服务器仅部署 Agent，Hub 通过 SSH 主动连入 Agent 采集数据。

**部署方式**：采用 SSH 模式（非 WebSocket 模式），Agent 仅监听端口等待 Hub 连接，无需知道 Hub 地址。

```yaml
# services/beszel-agent/compose.yaml
services:
  beszel-agent:
    image: henrygd/beszel-agent
    container_name: beszel-agent
    restart: unless-stopped
    network_mode: host
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
    environment:
      KEY: "<beszel hub 生成的 SSH 公钥>"
      PORT: "<监听端口>"
```

**关键要点：**

| 项 | 说明 |
|---|---|
| `network_mode: host` | 必须用 host 模式，否则无法正确采集宿主机网络和磁盘指标 |
| `docker.sock` 挂载 | 只读挂载，Agent 通过它采集 Docker 容器状态 |
| `KEY` | Beszel Hub 添加系统时生成的 SSH 公钥，用于 Hub → Agent 的 SSH 认证 |
| `PORT` | Agent 监听端口，每台服务器可不同；Hub 添加时填对应端口即可 |
| 无 `HUB_URL` / `TOKEN` | SSH 模式下不需要；WebSocket 模式（Agent 主动连 Hub）仅在内网域名可解析时使用 |

**部署位置**：`/srv/workspace/services/beszel-agent/compose.yaml`（属于 services 而非 infra，因为是监控服务而非基础网络设施）

**新增服务器部署流程：**

1. 在 R0 的 Beszel Hub 面板中添加新系统，获取 `KEY` 和分配的端口
2. 在目标服务器创建 `services/beszel-agent/compose.yaml`
3. `docker compose up -d`
4. 确认日志中出现 `Starting SSH server` 和 `SSH connection established`
5. 回到 Hub 面板确认数据上报正常
6. 安全组放行对应端口（仅对 Hub 所在网络的出口 IP 开放）

> ⚠️ Agent 启动时会出现 `WARN: HUB_URL environment variable not set`，这是正常的——SSH 模式不需要该变量。

### 2.8 项目接入

新增项目标准流程：

1. 根据项目类型在 `websites/` 或 `services/` 下创建项目目录
2. 编写 `docker-compose.yml`，加入 `proxy-network`，**不暴露端口到宿主机**
3. 设置唯一且语义清晰的 `container_name`
4. 若项目接入共享 `infra-redis`，先查 `redis_db_registry`，分配并登记唯一 `REDIS_DB`，再写入配置
5. 在 `Caddyfile` 中添加域名反代配置（仅 websites 类项目需要）
6. 热重载 Caddy
7. 启动项目容器
8. 上线验证（Redis 项目需额外确认业务写入进入预期 DB）

compose 模板：

```yaml
services:
  project_a-web:
    image: ...
    container_name: project_a-web
    restart: always
    env_file:
      - .env
    networks:
      - proxy-network

networks:
  proxy-network:
    external: true
```

---

## 三、运维规范

### 3.1 安全要求

- 数据库与缓存服务禁止对外暴露端口（仅绑定 `127.0.0.1`，供 SSH 隧道访问）
- SSH 建议使用密钥登录，禁用密码登录，修改默认端口
- 云服务器统一不启用 ufw，通过云厂商安全组管控入站，避免与 Docker iptables 规则冲突
- 定期更新 Caddy、PostgreSQL、MariaDB、Redis 镜像
- 证书目录 `infra/caddy/data/` 定期备份
- 所有凭据通过 `.env` 文件注入，禁止在 compose 中硬编码密码

### 3.2 凭据与交付规范

**1. 凭据统一入库（强制）**
任何新增的第三方接口、Token 或授权信息，必须第一时间写入 `devops.credentials` 表，不得仅存在于环境变量或本地文件中（包括 TG/QQ/微信等对话界面，TG 端需阅后即焚）。新项目配置数据库后，对应的连接信息同样必须入库，便于后续审计或交接查阅。

**2. 项目文档同步 Notion（按需，用户要求时执行）**
项目文档在写入 `/srv/workspace/docs/` 对应目录的同时，同步到 Notion 对应页面。Notion Token 从 `devops.credentials` 表中获取。

**3. 代码提交 GitHub（按需，用户要求时执行）**
代码变更同步提交到对应的 GitHub 仓库，用于备份与审计。GitHub Token 从 `devops.credentials` 表中获取（字段名 `github_token_classic`）。

**4. Cloudflare / GitHub 凭据路由**
当 Agent 遇到 Cloudflare / DNS 解析 / Workers / Pages 等任务时，应优先从 `devops.credentials` 中读取 `cloudflare_token`，再调用 Cloudflare API，而不是先向用户索要凭据。

当 Agent 遇到 GitHub 相关操作时，应优先从 `devops.credentials` 中读取 `github_token_classic`，刷新本地认证后再继续执行。

### 3.3 运维纪律与常见陷阱

1. **停用项目必须走完整清理 SOP，不得遗漏**

   #### 项目下线标准流程（AI Agent 必须严格遵守）

   **第一步：收到下线指令后，禁止立即执行。** 先全面排查并列出清单，向用户呈现以下选项：

   ```
   项目 [project_name] 下线清理方案，请选择：

   A. 仅停止容器（保留所有文件，可随时恢复）
   B. 停止容器 + 清理部署目录（websites/services 下的 compose、.env）
      保留：源码 code/、Caddy 配置、证书、CF DNS 记录
   C. B 的基础上 + 清理源码（code/ 目录）
      保留：Caddy 配置、证书、CF DNS 记录
   D. 全量清理（推荐彻底下线时使用），包含：
      - 停止并删除容器、镜像、volume
      - 删除 websites/ 或 services/ 下的部署目录
      - 删除 code/ 下的源码目录
      - 清除 Caddyfile 中的域名路由
      - 删除 Caddy data/ 中对应的证书文件
      - 通过 Cloudflare API 删除对应的 DNS 解析记录
      - 删除 PostgreSQL / MariaDB 中该项目的数据库和用户（需二次确认，不可逆）
      - 将 Redis DB 登记状态改为 retired，并清空对应 DB 数据（如有）
      - 在 devops.devops_logs 写入变更记录

   以上哪项？或自定义说明需要保留/清理的内容。
   ```

   **第二步：用户确认选项后，按选项逐项执行，执行完每项后报告结果。**

   **第三步：执行完成后，输出最终清单确认**，包括：
   - 已删除的文件/目录列表
   - 已删除的容器/镜像/volume
   - Caddyfile 变更情况（热重载是否成功）
   - CF DNS 记录删除情况（如执行了 D 选项）
   - 证书文件清理情况

   #### Cloudflare DNS 清理（D 选项）

   Agent 应从 `devops.credentials` 中读取 `cloudflare_token`，通过 Cloudflare API 查找并删除对应域名的 DNS 记录，**不得要求用户手动操作 CF 控制台**。

   #### 常见遗漏项（必须逐一核查）

   - Caddy `config/` 目录中自动保存的配置（caddy 会在运行时写入）
   - 证书目录：`infra/caddy/data/caddy/certificates/` 下以域名命名的子目录
   - `devops.redis_db_registry` 中对应 DB 状态
   - 其他项目的 compose 或 `.env` 中是否引用了该项目的容器名/服务

2. **禁止直接在运行时环境里"顺手改"容器状态**
   所有长期状态必须回写到对应的 compose 文件中：
   - `/srv/workspace/services/.../docker-compose.yml`
   - `/srv/workspace/websites/.../docker-compose.yml`
   - `/srv/workspace/infra/.../docker-compose.yml`

3. **源码目录与 compose build.context 必须严格对应 `code/`**
   禁止引用旧历史目录、迁移前目录、个人临时目录。

4. **容器重建后必须验证 Docker 元数据是否干净**

   ```bash
   docker system df
   docker network inspect proxy-network
   ```

   如果 `docker system df` 报 `rw layer snapshot not found`，需逐个重建相关容器。

5. **`.env` 文件与 compose 环境变量必须保持一致**
   禁止在 compose 中硬编码环境变量值，统一通过 `.env` 文件注入。`.env` 变更后必须重启对应容器才能生效。

6. **磁盘空间需定期关注**
   Docker 镜像、构建缓存、未清理的 volume 会持续累积。定期执行 `docker builder prune` 清理构建缓存（安全）；`docker system prune` 清理停止的容器和悬空镜像（谨慎使用 `-a`）。

7. **每次变更必须留审计痕迹**
   所有运维变更统一写入 `devops.devops_logs` 表，不再维护 `.md` 格式的运维日志文件。
   每条记录至少包含：`timestamp`、`project_name`、`change_type`、`reason`、`files_changed`、`verification`、`operator`、`status`。
   以下操作也属于必须入库的审计事件：Redis DB 编号分配/调整/回收、项目上下线、基础设施配置变更。
   如用户明确要求不写日志，按用户要求跳过。

---

## 四、检查清单

当用户要求"全面检查"时，按以下清单逐项确认：

- [ ] Docker 服务运行正常，`docker ps` 无异常退出容器
- [ ] 共享网络 `proxy-network` 存在且所有相关容器已加入
- [ ] Docker 日志轮转已配置（`/etc/docker/daemon.json`）
- [ ] `infra/caddy` 运行正常，`extra_hosts` 包含 `host-gateway`，Caddyfile 中无指向已停用项目的残留路由
- [ ] 若 Caddy 反代宿主机服务，upstream 使用 `host.docker.internal:<port>` 而非 `127.0.0.1:<port>`，并已验证容器内可达
- [ ] `infra/postgres` 运行正常，`devops` 数据库及 `credentials`、`devops_logs`、`redis_db_registry` 表存在
- [ ] `infra/mariadb` 运行正常（如已启用）
- [ ] `infra/redis` 运行正常
- [ ] `beszel-agent` 运行正常（`services/beszel-agent/`），日志中有 `SSH connection established`，Hub 面板数据上报正常
- [ ] 各项目容器均未对外暴露端口，仅通过 proxy-network 通信
- [ ] ufw 状态为 inactive（云服务器统一关闭，由云厂商安全组管控）
- [ ] 磁盘空间充足（`df -h`），无异常大文件堆积
- [ ] `docker system df` 无脏数据（如 `rw layer snapshot not found`）
- [ ] 各项目 compose 中 `build.context` 指向正确的 `code/` 目录，`.env` 文件与实际运行的环境变量一致
