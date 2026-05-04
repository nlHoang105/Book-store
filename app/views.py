from django.shortcuts import render,redirect
from django.db.models import Q
from django.http import HttpResponse,JsonResponse
from .models import *
import os
import re
import google.generativeai as genai
import json
import datetime
from .form import RegisterForm
from django.core.cache import cache
from django.shortcuts import get_object_or_404
from django.contrib.auth.forms import UserCreationForm
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import authenticate,login,logout
from django.db.models import Sum
from django.contrib import messages
from rapidfuzz import process,fuzz
from dotenv import load_dotenv
from django import template
from django.views.decorators.http import require_POST

@staff_member_required
def admin_dashboard(request):

    total_revenue = Order.objects.filter(complete=True).aggregate(Sum('total_price'))['total_price__sum'] or 0


    total_orders = Order.objects.filter(complete=True).count()


    top_products = Product.objects.order_by('-sold')[:5]


    recent_orders = Order.objects.order_by('-date_order')[:10]

    context = {
        'total_revenue': total_revenue,
        'total_orders': total_orders,
        'top_products': top_products,
        'recent_orders': recent_orders,
    }
    return render(request, 'app/admin_dashboard.html', context)
load_dotenv()
api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    raise ValueError("Không tìm thấy GEMINI_API_KEY trong .env") 
genai.configure(api_key=api_key)

def chatbot_api(request):
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)
        
    try:
        data = json.loads(request.body)
        user_message = data.get("message", "").strip()
        
        
        stop_words = ["tìm", "cho", "tôi", "sách", "về", "muốn"]
        keywords = [word for word in user_message.lower().split() if word not in stop_words]
        
        query = Q()
        if keywords:
            for word in keywords[:3]: 
                query |= Q(name__icontains=word) | Q(category__name__icontains=word)
        
        
        products = Product.objects.filter(query).distinct()[:5]
        
        
        product_list = [f"- [{p.name}](/detail/{p.id}/): {p.final_price}đ" for p in products]
        context_str = "\n".join(product_list) if product_list else "Hết hàng hoặc không có sách này."

        
        model = genai.GenerativeModel(
            model_name="gemini-3.1-flash-lite-preview", 
            system_instruction="Bạn là nhân viên tiệm sách BookStore. Chỉ tư vấn dựa trên danh sách sách được cung cấp. Luôn dùng Markdown để gửi link."
        )
        
        prompt = f"Khách hỏi: {user_message}\nKho sách hiện có:\n{context_str}"
        response = model.generate_content(prompt)
        
        return JsonResponse({"reply": response.text})

    except Exception as e:
        return JsonResponse({"reply": "Xin lỗi, hệ thống đang bận."}, status=500)
        

custom_filters = template.Library()


@custom_filters.filter
def vnd(value):
    try:
        return f"{int(value):,}".replace(",", ".")
    except:
        return value

def user_orders(request):
    if request.user.is_authenticated:
        
        orders = Order.objects.filter(customer=request.user).order_by('-date_order')
    else:
        
        device = request.session.session_key
        orders = Order.objects.filter(session_key=device).order_by('-date_order')
    
    context = {'orders': orders}
    return render(request, 'app/user_orders.html', context)

def cancel_order(request, order_id):
    order = get_object_or_404(Order, id=order_id)
    
    
    if order.customer == request.user and order.status == 'Pending':
        order.status = 'Cancelled'
        order.save()
        messages.success(request, f'Đã hủy đơn hàng #{order.id} thành công.')
    else:
        messages.error(request, 'Bạn không thể hủy đơn hàng này.')
        
    return redirect('user_orders')

def order_detail(request, order_id):
    
    order = get_object_or_404(Order, id=order_id)
    
    
    if order.customer != request.user:
        return render(request, 'app/404.html') 

   
    items = order.items.all()
    
    context = {
        'order': order,
        'items': items,
    }
    return render(request, 'app/order_detail.html', context)
def detail(request, id):
    order = get_order(request)
    product = get_object_or_404(Product, id=id)

    if order:
        items = order.items.all()
        cartItems = order.get_cart_items
    else:
        items = []
        cartItems = 0
        
        order = {'get_cart_total': 0, 'get_cart_items': 0}

    context = {
        'product': product,
        'items': items,
        'order': order,
        'cartItems': cartItems,
    }
    return render(request, 'app/detail.html', context)

def search_suggestions(request):
    q = request.GET.get("q", "")
    products = Product.objects.filter(name__icontains=q)[:8]

    data = [
        {
            "id": p.id,
            "name": p.name,
            "image": p.image.name.split('/')[-1] if p.image else "",
            "price": p.price if hasattr(p, 'price') else 0,
        }
        for p in products
    ]
    return JsonResponse(data, safe=False)

def search_fuzzy(request):
    query = request.GET.get('q', '')
    if len(query) < 2:
        return render(request, 'search.html', {'products': []})

    words = query.split()
    search_query = Q()
    for word in words:
        search_query |= Q(name__icontains=word)
    
    candidates = Product.objects.filter(search_query)[:50] 

    
    results = []
    for p in candidates:
        score = fuzz.partial_ratio(query.lower(), p.name.lower())
        if score > 60:
            p.search_score = score 
            results.append(p)
    
    results.sort(key=lambda x: x.search_score, reverse=True)

    return render(request, 'search.html', {'products': results})

def category_view(request, category_slug=None):
  
    order = get_order(request)
    cartItems = order.get_cart_items if (order and hasattr(order, 'id')) else 0
    
    
    all_main_categories = Category.objects.filter(sub_category__isnull=True)
    
    
    if category_slug:
        active_category = get_object_or_404(Category, slug=category_slug)
        
        sub_categories = active_category.sub_categories.all()
        products = Product.objects.filter(
            Q(category=active_category) | Q(category__in=sub_categories)
        ).distinct()
    else:
        active_category = None
        products = Product.objects.all()

  
    price_filters = request.GET.getlist('price') 
    if price_filters:
        price_queries = Q()
        for price in price_filters:
            if price == "0-150":
                price_queries |= Q(price__lte=150000)
            elif price == "150-300":
                price_queries |= Q(price__gte=150000, price__lte=300000)
            elif price == "300-500":
                price_queries |= Q(price__gte=300000, price__lte=500000)
            elif price == "500-700":
                price_queries |= Q(price__gte=500000, price__lte=700000)
            elif price == "700+":
                price_queries |= Q(price__gte=700000)
        
        products = products.filter(price_queries)

    price_range_labels = [
        ('0-150', '0đ - 150,000đ'),
        ('150-300', '150,000đ - 300,000đ'),
        ('300-500', '300,000đ - 500,000đ'),
        ('500-700', '500,000đ - 700,000đ'),
        ('700+', '700,000đ - Trở lên'),
    ]

    sort_by = request.GET.get('sort')
    if sort_by == 'price_asc':
        products = products.order_by('price')
    elif sort_by == 'price_desc':
        products = products.order_by('-price')
    else:
        products = products.order_by('-id') 

    context = {
        'products': products.only('name', 'price', 'image', 'sale_percent'), 
        'active_category': active_category,
        'all_categories': all_main_categories, 
        'selected_prices': price_filters, 
        'order': order if (order and hasattr(order, 'id')) else {'get_cart_total': 0},
        'cartItems': cartItems,
        'price_range_labels': price_range_labels,
    }
    return render(request, 'app/category.html', context)
def search(request):
    if request.method == "POST":
        searched = request.POST["searched"]
        keys = Product.objects.filter(name__contains = searched)
    if request.user.is_authenticated:
        customer = request.user
        order, created = Order.objects.get_or_create(customer =customer,complete =False)
        items = order.items.all()
        cartItems = order.get_cart_items
    else:
        items =[]
        order = {'get_cart_items' :0,'get_cart_total': 0}
        cartItems = order['get_cart_items']
    products = Product.objects.all()
    return render(request,'app/search.html',{"searched":searched,"keys":keys,'products': products,'cartItems':cartItems})
def register(request):
    if request.method == "POST":
        form = RegisterForm(request.POST) 
        if form.is_valid():
            form.save()
            messages.success(request, "Đăng ký thành công!")
            return redirect('login')
    else:
        form = RegisterForm()
        
    context = {'form': form}
    return render(request, 'app/register.html', context)
def loginPage(request):
    if request.user.is_authenticated:
        return redirect('home')

    if request.method == "POST":
        username = request.POST.get('username')
        password = request.POST.get('password')

       
        failed_attempts_key = f"failed_attempts_{username}"
        is_locked_key = f"is_locked_{username}"

       
        if cache.get(is_locked_key):
            messages.error(request, 'Tài khoản này đã bị khóa 30 phút do nhập sai quá nhiều!')
            return render(request, 'app/login.html', {})

        user = authenticate(request, username=username, password=password)

        if user is not None:
          
            cache.delete(failed_attempts_key)
            login(request, user)
            return redirect('home')
        else:
          
            attempts = cache.get(failed_attempts_key, 0) + 1
            cache.set(failed_attempts_key, attempts, timeout=1800)

            if attempts >= 3:
              
                cache.set(is_locked_key, True, timeout=1800)
                messages.error(request, 'Bạn đã nhập sai 3 lần. Tài khoản bị khóa 30 phút!')
            else:
                messages.info(request, f'Tên đăng nhập hoặc mật khẩu không đúng! (Lần {attempts}/3)')

    context = {}
    return render(request, 'app/login.html', context)
def logoutPage(request):
    logout(request)
    return redirect('login')
def home(request):
    order = get_order(request)
    
    if order and hasattr(order, 'pk'):
        
        items = order.items.all() 
        cartItems = order.get_cart_items
    else:
        
        items = []
        cartItems = 0
        order = {'get_cart_total': 0, 'get_cart_items': 0}

   
    tham_khao_products = Product.objects.filter(category__slug='Stk').only('name', 'price', 'image')
    products = Product.objects.all().only('name', 'price', 'image', 'sale_percent')

 
    context = {
        'tham_khao_products': tham_khao_products,
        'products': products,
        'items': items,
        'cartItems': cartItems,
        'order': order, 
    }
    return render(request, 'app/home.html', context)

def cart(request):
    order = get_order(request)

    
    if order and hasattr(order, 'pk'):
       
        if not request.user.is_authenticated:
            sync_cookie_cart_to_order(request, order)
        
        
        items = order.items.all()
        cartItems = order.get_cart_items
    else:
        items = []
        cartItems = 0
        order = {'get_cart_total': 0, 'get_cart_items': 0}

    response = render(request, 'app/cart.html', {
        'items': items,
        'order': order,
        'cartItems': cartItems,
    })

    if not request.user.is_authenticated and response:
        response.delete_cookie('cart')

    return response
def checkout(request):
    order = get_order(request)
    if not order:
        return redirect('cart')

    if not request.user.is_authenticated:
        sync_cookie_cart_to_order(request, order)

    selected_ids = request.session.get("checkout_items", [])
    items = order.items.filter(id__in=selected_ids) if selected_ids else order.items.all()

    if not items.exists():
        return redirect('cart')

    if request.method == "POST" and "address" in request.POST:
        name = request.POST.get('name')
        mobile = request.POST.get('mobile')
        city = request.POST.get('city')
        address = request.POST.get('address')
        payment_method = request.POST.get('payment_method') 

        if name and mobile and city and address:
            ShoppingAddress.objects.create(
                customer=request.user if request.user.is_authenticated else None,
                order=order,
                name=name, mobile=mobile, city=city, address=address
            )
            
           
            order.total_price = order.get_cart_total 
         

            order.transaction_id = f"{datetime.datetime.now().strftime('%Y%m%d')}-{order.id}"
            order.complete = True
            order.status = 'Pending'
            order.payment_method = payment_method 
            order.save() 
            
            if 'checkout_items' in request.session:
                del request.session['checkout_items']
            
            if payment_method == 'bank':
                return redirect('payment_gateway', order_id=order.id)
            
            response = redirect('order_success', order_id=order.id)
            response.delete_cookie('cart')
            return response

    context = {"items": items, "order": order, "cartItems": items.count()}
    return render(request, "app/checkout.html", context)

def payment_gateway(request, order_id):
    bank_id = "VCB" 
    account_no = "123456789"
    order = Order.objects.get(id=order_id)
    amount = order.get_cart_total 
    description = f"THANH TOAN DON HANG {order.transaction_id}"
    
    qr_url = f"https://img.vietqr.io/image/{bank_id}-{account_no}-compact.png?amount={amount}&addInfo={description}"
    
    return render(request, "app/payment.html", {"qr_url": qr_url, "order": order})

def order_success(request, order_id):
    return render(request, 'app/order_success.html', {'order_id': order_id})


def get_order(request):
    
    if request.user.is_authenticated:
        
        return Order.objects.filter(customer=request.user, complete=False).first()
    else:
        if not request.session.session_key:
            return None 
        return Order.objects.filter(session_key=request.session.session_key, complete=False).first()
def sync_cookie_cart_to_order(request, order):
    if not order:
        return
    cart_json = request.COOKIES.get('cart', '{}')
    try:
        cart = json.loads(cart_json)
    except:
        cart = {}

    for product_id, item in cart.items():
        try:
            product = Product.objects.get(id=product_id)
            orderItem, created = OrderItem.objects.get_or_create(
                order=order,
                product=product
            )
            orderItem.quantity = item['quantity']
            orderItem.save()
        except Product.DoesNotExist:
            continue
@require_POST
def updateItem(request):
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    productId = data.get('productId')
    action = data.get('action')

    if not productId or not action:
        return JsonResponse({'error': 'Missing productId or action'}, status=400)

    product = get_object_or_404(Product, id=productId)

    
    if request.user.is_authenticated:
        order, created = Order.objects.get_or_create(customer=request.user, complete=False)
    else:
      
        if not request.session.session_key:
            request.session.create()
        session_id = request.session.session_key
        order, created = Order.objects.get_or_create(session_key=session_id, complete=False)

   
    orderItem, created = OrderItem.objects.get_or_create(
        order=order, 
        product=product,
        defaults={'price_at_order': product.price} 
    )

   
    if action == 'add':
        orderItem.quantity += 1
    elif action == 'remove':
        orderItem.quantity -= 1
    elif action == 'delete':
        orderItem.quantity = 0 
    else:
        return JsonResponse({'error': 'Invalid action'}, status=400)

   
    if orderItem.quantity <= 0:
        orderItem.delete()
    else:
        orderItem.save()

   
    return JsonResponse({
        'status': 'ok',
        'cartItems': order.get_cart_items
    })