{% set reduced_name = fullname.split(".", 1)[-1] if fullname.startswith("ombootstrap.") else fullname %}

{{ fullname | escape | underline }}

.. rubric:: Description

.. automodule:: {{ fullname }}
   :members:
   :undoc-members:

.. currentmodule:: {{ fullname }}




{% if classes %}
.. rubric:: Classes

.. autosummary::
    :toctree: .
    {% for class in classes %}
    {{ class }}
    {% endfor %}

{% endif %}

{% if functions %}
.. rubric:: Functions

.. autosummary::
    :toctree: .
    {% for function in functions %}
    {{ function }}
    {% endfor %}

{% endif %}
