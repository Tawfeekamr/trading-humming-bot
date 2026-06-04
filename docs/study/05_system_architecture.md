# Chapter 4: System Architecture and Implementation

This chapter shifts from theory to software engineering, detailing how the framework was built and deployed.

## 4.1 High-Level Architecture
* Provide an architecture diagram (can use Mermaid.js or an image).
* Explain the separation of concerns: Data Feed -> ML Inference -> Strategy Engine -> Execution Engine (Hummingbot) -> Exchange API.

## 4.2 Hummingbot v2 Integration
* Explain how the custom Python scripts (`ta_grid_trend.py`) interface with the Hummingbot core architecture.
* Discuss the advantages of using Hummingbot (order management, rate limiting, standardized exchange connectors) over building a custom execution engine from scratch.

## 4.3 Cloud Infrastructure and CI/CD Pipeline
* **AWS Deployment:** Rationale for using AWS EC2 in Tokyo (latency optimization to Asian exchange servers).
* **Containerization:** The role of Docker and `docker-compose` in ensuring environment consistency between development and production.
* **GitHub Actions:** Detailed breakdown of the automated deployment pipeline (Push to Main -> Build -> Deploy via AWS SSM).

## 4.4 Automated Retraining and Hot-Reloading
* **The Problem:** Financial models decay over time (Concept Drift).
* **The Solution:** The `retrain.yml` pipeline that triggers monthly model retraining.
* **Hot-Reloading:** How the Python architecture detects new `.pkl` model files and loads them into memory without restarting the active trading loops, ensuring zero downtime.

## 4.5 Monitoring and Telemetry
* The Telegram bot integration.
* Asynchronous alert systems, daily P&L reporting, and interactive commands (`/status`, `/pnl`).
