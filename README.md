# ecs-scaler

https://hub.docker.com/r/theherk/ecs-scaler/

A small, opinionated tool used to scale in and out ECS services. This will match all clusters with "-ENV" or "ENV-" in the name, retrieve all services running in those clusters, then scale them to min / max given. You can filter out results using `-e svcA -e svcB` or `--exclude svcA --exclude svcB`. Or to start with no matches and include specifically, `-i svcC -i svcD` or `--include svcC --include svcD`.

## Usage

    ./scale.py [env] [options]

#### Example (scale all services in dev to min 2 max 4 except services matching reverse-proxy):

    ./scale.py dev -e reverse-proxy --min 2 --max 4

#### Example (list all services that match excluding reverse-proxy):

    ./scale.py dev -e reverse-proxy -l

### Usage with docker

This provides a docker image that can simplify the process.

#### Using a local copy of the repository

    ARGS="scale dev -e reverse-proxy -l" make run

#### Without make

    docker run -it -v $(HOME)/.aws:/root/.aws:ro scale dev -e reverse-proxy -l

_note: You are not required to mount credentials, but the program will expect to be allowed to make api calls._

#### Directly from docker hub.

    docker run -it -v $(HOME)/.aws:/root/.aws:ro theherk/ecs-scaler scale dev -e reverse-proxy -l
