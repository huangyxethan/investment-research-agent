# 部署：把投研 Agent 发布成带登录的在线链接

免费路径：**Streamlit Community Cloud**(托管 app)+ **Neon**(托管 Postgres)+
**streamlit-authenticator**(登录 + 双角色)。全程公开数据,无专有数据泄露风险。

> 角色：`admin` 看叙述+图+**SQL**+完整数据；`viewer` 看叙述+图+截断数据(隐藏 SQL)。
> 改角色差异：编辑 `web/app.py` 末尾 `if role == "admin"` 分支。

---

## 1. 建 Neon 云数据库

1. 注册 https://neon.tech → New Project(区域选离你近的,如 Singapore)。
2. 复制连接串(形如)：
   `postgresql://USER:PASSWORD@ep-xxx.ap-southeast-1.aws.neon.tech/neondb?sslmode=require`
3. Neon 默认支持 pgvector,无需额外开通。

## 2. 把本地数据导入 Neon（一条命令，全程在容器内，避免本地 psql/编码问题）

确保本地 `invest_pg` 容器在运行,然后把下面的 `<NEON_URL>` 换成第 1 步的连接串：

```powershell
# Windows PowerShell（docker 在 PATH 上；否则前缀 "C:\Program Files\Docker\Docker\resources\bin\"）
docker exec invest_pg sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner --no-privileges | psql "<NEON_URL>"'
```

pg_dump 与 psql 都用容器自带、版本匹配的客户端,pipe 留在容器内的 Linux shell,
不经过 PowerShell(避免编码损坏)。完成后可抽查：

```powershell
docker exec invest_pg psql "<NEON_URL>" -c "SELECT count(*) FROM dim_fund;"
```

> 注意：以后本地重跑 ETL 刷新数据后,需重新执行本步把新数据推到 Neon。

## 3. 生成登录密码哈希

```powershell
.venv\Scripts\python.exe scripts\gen_password_hash.py 你的管理员密码 你的访客密码
```

复制打印出的两个 `$2b$...` 哈希备用(切勿提交明文密码)。

## 4. 在 Streamlit Community Cloud 部署

1. 注册 https://share.streamlit.io(用你的 GitHub 账号),授权访问私有库
   `huangyxethan/investment-research-agent`。
2. New app → 选该库、分支 `main`、**Main file path = `web/app.py`**。
3. 展开 **Advanced settings → Secrets**,按 `.streamlit/secrets.toml.example` 粘贴
   (填入 Neon 连接串、LLM key、第 3 步的哈希、随机 cookie_key)：

   ```toml
   PG_CONN_STR = "postgresql://...neon...?sslmode=require"
   DEEPSEEK_API_KEY = "sk-..."
   ANTHROPIC_API_KEY = "sk-ant-..."   # 可选
   cookie_key = "一段随机长字符串"

   [auth.credentials.usernames.admin]
   name = "管理员"
   email = "admin@example.com"
   password = "$2b$12$...管理员哈希..."
   role = "admin"

   [auth.credentials.usernames.viewer]
   name = "访客"
   email = "viewer@example.com"
   password = "$2b$12$...访客哈希..."
   role = "viewer"
   ```

4. Deploy。Streamlit Cloud 会装根目录 `requirements.txt`(已瘦身,仅 app 运行时依赖,
   不含 torch/dagster,构建快)。
5. 几分钟后得到公网链接,发给领导。两个账号分别登录验证权限差异。

## 5. 运维须知

- **密钥**：只放在 Cloud 的 Secrets 里,绝不进 git。`.env`、`.streamlit/secrets.toml`
  均已 gitignore。
- **依赖**：`requirements.txt` = app 运行时(Cloud 用);本地数据/ETL 用
  `pip install -r requirements-etl.txt`(含 akshare/dagster/torch 等)。
- **免费档限制**：闲置会休眠(再访问冷启动几秒),适合领导偶尔试用;团队高频使用再升级
  到 Render/Railway 付费档。
- **成本**：有登录把关,仅 2 个账号,LLM token 开销可控。
- **未来**：换成 FastAPI + React canvas 时,Neon/账号/密钥/平台都复用,只替换前端层;
  `agent/` 核心保持 UI 无关。
