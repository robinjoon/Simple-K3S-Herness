#!/usr/bin/env python3
import argparse
import json
import re

try:
    from tools import platform
except ModuleNotFoundError:
    import platform


IMAGE_TAG = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}")


def validate_tag(tag):
    if not IMAGE_TAG.fullmatch(tag or ""):
        platform.fail("Image tag must use OCI tag characters and be 128 characters or fewer.")
    if tag.lower() == "latest":
        platform.fail("The mutable 'latest' tag is not allowed for releases.")


def set_image_tag(args):
    validate_tag(args.tag)
    values_file = platform.values_file_for(args.name)
    values = platform.load_json(values_file)
    platform.ensure_workload_values(values, args.name)

    workload = values.get("workload")
    containers = workload.get("containers") if isinstance(workload, dict) else None
    if not isinstance(containers, list):
        platform.fail("workload.containers must be an array.")

    matches = [
        container for container in containers
        if isinstance(container, dict) and container.get("name") == args.container
    ]
    if len(matches) != 1:
        platform.fail(
            f"Expected exactly one container named {args.container!r}; found {len(matches)}."
        )

    image = matches[0].get("image")
    if not isinstance(image, dict) or not isinstance(image.get("repository"), str) or not image["repository"]:
        platform.fail(f"Container {args.container!r} must have a non-empty image repository.")

    previous_tag = image.get("tag")
    if previous_tag == args.tag:
        print(f"{args.name}/{args.container} already uses image tag {args.tag}.")
        return

    image["tag"] = args.tag
    result = platform.lint_values(values)
    if result.returncode != 0:
        platform.print_command_failure("Release validation failed.", result)
        raise SystemExit(1)

    values_file.write_text(json.dumps(values, indent=2) + "\n")
    print(f"Updated {args.name}/{args.container} image tag from {previous_tag} to {args.tag}.")


def main():
    parser = argparse.ArgumentParser(
        description="Update one existing workload image tag for the CI release flow."
    )
    parser.add_argument("name", help="Existing workload name")
    parser.add_argument("--container", required=True, help="Existing container name")
    parser.add_argument("--tag", required=True, help="New immutable image tag")
    set_image_tag(parser.parse_args())


if __name__ == "__main__":
    main()
