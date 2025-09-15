#!/bin/bash

# List of broken pods (update this as needed based on `kubectl get pods`)
broken_apps=("celery-beat" "celery-worker" "nginx" "web")

for app in "${broken_apps[@]}"; do
  # Find the deployment YAML file for this app
  yamlfile="$(ls *-deployment.yaml | grep "$app")"
  if [[ -n "$yamlfile" ]]; then
    echo "Processing $yamlfile"
    # Insert or replace imagePullPolicy: Never after image: line, with correct indentation
    awk '
      # Match "image:" line and capture its indentation
      /^(\s*)image:/ {
        print $0
        indent = gensub(/^(\s*).*/, "\\1", "g", $0)
        getline nextline
        if (nextline ~ /^[ ]*imagePullPolicy:/) {
          print indent "imagePullPolicy: Never"
        } else {
          print indent "imagePullPolicy: Never"
          print nextline
        }
        next
      }
      /^[ ]*imagePullPolicy:/ { next }
      { print }
    ' "$yamlfile" > "$yamlfile.tmp" && mv "$yamlfile.tmp" "$yamlfile"
  fi
done
