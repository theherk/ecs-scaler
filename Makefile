.DEFAULT_GOAL := help

# project variables
IMAGE := ecs-scaler
IMAGES := $(shell docker images | awk '/$(IMAGE)/ {print $$3}')
RUNNING :=  $(shell docker ps | grep "$(IMAGE)" | awk '{print $$1}')
ALL := $(shell docker ps -a | grep "$(IMAGE)" | awk '{print $$1}')

help: ## display this usage message
	$(info available targets:)
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage:\n  make \033[36m\033[0m\n"} /^[$$()% a-zA-Z.\/_-]+:.*?##/ { printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2 } /^##@/ { printf "\n\033[1m%s\033[0m\n", substr($$0, 5) } ' $(MAKEFILE_LIST)

attach: clean-containers build ## attach to a running container interactively in bash
	docker run -it \
	--name $(IMAGE)-1 \
	--entrypoint /bin/bash \
	-v $(HOME)/.aws:/root/.aws:ro \
	$(IMAGE)

requirements.txt: ## generate requirements file
	pipenv requirements > $@

build: requirements.txt ## build the image
	docker build -t $(IMAGE) .

clean: clean-images ## clean all the things

clean-containers: stop-all ## remove containers
	for c in $(ALL); do docker rm $$c; done

clean-images: clean-containers ## remove images
	for c in $(IMAGES); do docker rmi $$c; done

publish: build ## publish the docker build to registry
	docker login -u theherk
	docker tag $(IMAGE):latest theherk/$(IMAGE):latest
	docker push theherk/$(IMAGE):latest

run: clean-containers build ## remove previous and run a new container
	docker run -it \
	--name $(IMAGE)-1 \
	-v $(HOME)/.aws:/root/.aws:ro \
	$(IMAGE) $(ARGS)

stop-all: ## stop all running containers of this image
	for c in $(RUNNING); do docker stop $$c; done
