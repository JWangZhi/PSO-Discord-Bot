# Infrastructure & Monitoring Plan

Ultimate goal: The system operates 24/7 stably at a **Zero-cost ($0)**.

## 1. Cloud Hosting Options

Eliminating the power-consuming personal machine, we deploy onto a Cloud VPS with an **Always Free** tier.

| Criteria | Oracle Cloud (OCI) | Google Cloud (GCP) |
| :--- | :--- | :--- |
| **Free Tier** | 4 ARM Cores, 24GB RAM, 200GB Storage | e2-micro (2 vCPU, 1GB RAM), 30GB Storage |
| **Evaluation** | **Extremely powerful**. Enough to run multiple Docker containers, Database, and monitoring smoothly. | **Modest**. Enough to run a basic Bot Script, requires careful RAM optimization. |
| **Drawbacks** | Hard to register, will be reclaimed if 20% capacity isn't used over 7 days. | Very little RAM (1GB), easy to Out-of-memory. No free static IPv4 (Since 2024). |

**Recommendation**: Try to register for **Oracle Cloud**. If that fails, use **GCP e2-micro** combined with a Webhook architecture (choose Serverless like AWS Lambda/Vercel if the Bot doesn't need a 24/7 websocket connection).

## 2. Database Tier

Do not install the database on the VPS to save resources; use Cloud Databases directly:
- **MongoDB Atlas (M0 Free Tier)**: 512MB storage. More than enough for storing user profiles, guild configurations, and short text chat histories.
- **Pinecone (Starter Plan)**: Currently the #1 Vector Database service. The Starter plan provides 1 Index and up to 2GB of capacity (Enough to hold hundreds of thousands of PSO2 Wiki entries).

## 3. Monitoring Stack

Losing control on the Cloud is a disaster. We need "the All-Seeing Eye".

- **Grafana Dashboards**: Platform for beautifully visual charts.
- **Prometheus**: Embedded within the Bot's Python code, gathering Metrics:
  - *Token Tracker*: Total Input/Output Tokens used per day for Gemini/Groq. If it reaches danger levels (running out of free tier) -> Flag warning.
  - *Latency Check*: Measure the speed of API Providers.
  - *Command Usage*: Frequency of calling `/ask`, `/phashion`.
- **Bot Uptime**: Use the UptimeRobot service to periodically ping and ensure the Bot hasn't "fallen asleep".
