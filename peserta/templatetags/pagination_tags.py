# peserta/templatetags/pagination_tags.py
from django import template

register = template.Library()


@register.simple_tag
def pagination_range(page_obj, on_each_side=1, on_ends=1):
    """
    Return list of page numbers + '...' untuk pagination.
    
    Contoh hasil untuk page=4 dari 174, on_each_side=1, on_ends=1:
      [1, '...', 3, 4, 5, '...', 174]
    
    Contoh hasil untuk page=50 dari 174:
      [1, '...', 49, 50, 51, '...', 174]
    
    on_each_side = berapa angka di kiri & kanan halaman aktif
    on_ends      = berapa angka di awal & akhir (halaman 1 & N)
    """
    current = page_obj.number
    total = page_obj.paginator.num_pages

    if total <= (on_each_side * 2 + on_ends * 2 + 1):
        return list(range(1, total + 1))

    pages = set()
    # Awal
    for i in range(1, on_ends + 1):
        pages.add(i)
    # Akhir
    for i in range(total - on_ends + 1, total + 1):
        pages.add(i)
    # Sekitar halaman aktif
    for i in range(current - on_each_side, current + on_each_side + 1):
        if 1 <= i <= total:
            pages.add(i)

    sorted_pages = sorted(pages)
    result = []
    prev = 0
    for p in sorted_pages:
        if p - prev > 1:
            result.append('...')
        result.append(p)
        prev = p

    return result


@register.simple_tag(takes_context=True)
def query_string(context, **kwargs):
    """
    Build query string dengan mempertahankan parameter GET yang ada,
    kecuali yang di-override via kwargs.
    
    Contoh: {% query_string request page=3 %}
    → "?q=budi&status=aktif&page=3"
    """
    request = context.get('request')
    if not request:
        return ''

    query = request.GET.copy()
    for k, v in kwargs.items():
        if v is None or v == '':
            query.pop(k, None)
        else:
            query[k] = str(v)

    encoded = query.urlencode()
    return f'?{encoded}' if encoded else '?'


@register.simple_tag(takes_context=True)
def query_string_pages(context, page_num):
    """Shortcut: query string untuk pindah ke halaman tertentu."""
    return query_string(context, page=page_num)
