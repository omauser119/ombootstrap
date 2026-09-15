{% set reduced_name = fullname.split(".", 1)[-1] if fullname.startswith("ombootstrap.") else fullname %}
.. title: {{reduced_name}}


.. currentmodule:: {{ fullname }}
.. click:: {% if fullname == 'main' %}ombootstrap.main:cli{% else %}{{fullname}}.cli:cmd_{{reduced_name}}{% endif %}
  :prog: ombootstrap {{reduced_name}}
  :nested: full

