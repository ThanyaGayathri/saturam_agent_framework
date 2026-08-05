# egpt-infra

**A lightweight framework for managing agentic infrastructure**

[![Python 3.7+](https://img.shields.io/badge/python-3.7+-blue.svg)](https://www.python.org/downloads/)

## Overview

`egpt-infra` is the infrastructure management layer for EGPT. It provides a unified interface for managing infrastructure across multiple platforms including AWS, Docker, and local environments, making it easy to deploy agents and their supporting services.

## Key Features

- **Multi-Platform Support**: Manage infrastructure across AWS, Docker, and local environments
- **Agent-Focused**: Purpose-built for deploying agents and their supporting infrastructure
- **Template-Based**: Quick start with pre-built infrastructure templates
- **Resource Management**: Resource management for databases, networking, storage, and compute
- **Application Support**: Built-in support for FastAPI, Streamlit, Celery, Django, and more

## Installation

`egpt-infra` is published to a private channel as GitHub Release assets, not to public PyPI. Install it together with the rest of the first-party closure:

```bash
./scripts/install_from_github_release.sh egpt
```

This needs `GITHUB_TOKEN` (or `GH_TOKEN`) with `contents:read` on `Saturam-Inc/saturam-agent-framework`. Optional extras are `egpt-infra[aws]`, `egpt-infra[docker]` and `egpt-infra[dev]`; when installing from release assets, the underlying third-party requirements (`boto3`, `docker`) resolve from PyPI as usual.

## Command-line interface

The legacy `ag` and `agno` executables are no longer installed. Infrastructure CLI
integration with the branded `egpt` command is deferred to a separate release.

## Project Structure

```
egpt/
├── aws/                    # AWS resource management
│   ├── resource/           # AWS resource types (EC2, RDS, S3, etc.)
│   └── app/                # AWS application deployments
├── docker/                 # Docker resource management
│   ├── resource/           # Docker resources (containers, networks, volumes)
│   └── app/                # Dockerized applications
├── base/                   # Base classes and interfaces
├── cli/                    # Command-line interface
├── infra/                  # Core infrastructure management
└── utilities/              # Helper utilities and tools
```

## Supported Resources

### AWS Resources
- **Compute**: EC2 instances, ECS clusters, ECS services
- **Storage**: S3 buckets, EBS volumes
- **Database**: RDS instances and clusters
- **Networking**: VPC, subnets, security groups, load balancers
- **Security**: IAM roles and policies, ACM certificates
- **Analytics**: EMR clusters, Glue crawlers
- **Caching**: ElastiCache clusters

### Docker Resources
- **Containers**: Docker containers with full lifecycle management
- **Networks**: Custom Docker networks
- **Volumes**: Persistent and ephemeral volumes
- **Images**: Container image management

### Application Types
- **FastAPI**: REST API applications
- **Streamlit**: Data science and ML dashboards
- **Celery**: Distributed task processing
- **Django**: Web applications
- **PostgreSQL**: Database with pgvector support
- **Redis**: Caching and message brokering

## Requirements

- Python 3.7 or higher
- For AWS: Valid AWS credentials configured
- For Docker: Docker engine installed and running

## Documentation

Design documents and architecture decision records live in `docs/adr/` in the repository.

## Support

Open an issue on [GitHub](https://github.com/Saturam-Inc/saturam-agent-framework/issues) for bugs or feature requests.

## License

Apache-2.0. See the [LICENSE](LICENSE) file for details.
