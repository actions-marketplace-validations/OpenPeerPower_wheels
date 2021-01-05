---
title: "Wheels"
description: "List of pre-built-wheels."
sidebar: false
is_homepage: true
hide_github_edit: true
body_id: wheels-page
regenerate: false
---


{% assign whl_files = site.static_files | where: "wheel", true %}
{% for mywhl in whl_files %}
  [{{ mywhl.path }}]({{ mywhl.path }})
{% endfor %}
