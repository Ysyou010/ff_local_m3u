{% extends "base.html" %}
{% block content %}
<div>
  {{ macros.m_tab_head_start() }}
    {{ macros.m_tab_head('normal', '재생 목록', true) }}
  {{ macros.m_tab_head_end() }}

  {{ macros.m_tab_content_start() }}
  <div class="tab-pane active" id="normal">
    
    <ul class="nav nav-pills mb-3" id="category_tabs" role="tablist"></ul>

    <div id="list_div">
        <div class="text-center mt-5"><i class="fa fa-spinner fa-spin fa-2x"></i> 목록을 불러오는 중입니다...</div>
    </div>
    
  </div>
  {{ macros.m_tab_content_end() }}
</div>

<form name="playform">
    <input type="hidden" id="play_title" name="play_title">
    <input type="hidden" id="play_source_src" name="play_source_src">
    <input type="hidden" id="play_source_type" name="play_source_type">
</form>

<script type="text/javascript">
var current_data = [];
var categories = [];
var current_category = "전체";

$(document).ready(function(){
    console.log("[디버그] 재생 목록 AJAX 요청 시작");
    $.ajax({
        url: '/' + PACKAGE_NAME + '/ajax/get_list',
        type: "POST", 
        cache: false,
        data: {},
        dataType: "json",
        success: function (data) {
            console.log("[디버그] 서버 응답 수신 성공:", data);
            if (data.ret == "success") {
                current_data = data.list;
                extractCategories();
                renderTabs();
                make_list();
            } else {
                notify('목록 처리 실패: ' + data.msg, 'warning');
                $('#list_div').html('<div class="alert alert-warning"><b>서버 응답 실패:</b><pre>' + data.msg + '</pre></div>');
            }
        },
        // 🌟 무한 로딩의 원인을 화면에 강제로 띄워주는 에러 추적 블록 추가
        error: function (xhr, status, error) {
            console.error("[디버그] AJAX 통신 자체 실패!!", status, error);
            console.error("[디버그] 서버가 뱉은 응답 본문:", xhr.responseText);
            
            var errorHtml = '<div class="alert alert-danger">';
            errorHtml += '  <h4><i class="fa fa-exclamation-triangle"></i> 통신 실패 (무한로딩 원인 발견)</h4>';
            errorHtml += '  <p><b>HTTP 상태 코드:</b> ' + xhr.status + ' (' + error + ')</p>';
            errorHtml += '  <p><b>프레임워크 라우팅 상태:</b> ' + status + '</p>';
            errorHtml += '  <hr>';
            errorHtml += '  <p><b>서버가 반환한 에러 본문 내용:</b></p>';
            errorHtml += '  <pre style="background:#f8d7da; padding:10px;">' + (xhr.responseText ? xhr.responseText : '응답 본문이 비어있습니다.') + '</pre>';
            errorHtml += '</div>';
            
            $('#list_div').html(errorHtml);
            notify('서버 통신 에러 발생!', 'danger');
        }
    });
});

function extractCategories() {
    var catSet = new Set();
    catSet.add("전체");
    
    for (var i = 0; i < current_data.length; i++) {
        var name = current_data[i].name;
        var match = name.match(/^\[(.*?)\]\s(.*)$/);
        
        if (match) {
            current_data[i].category = match[1];
            current_data[i].display_title = match[2];
            catSet.add(match[1]);
        } else {
            current_data[i].category = "기본";
            current_data[i].display_title = name;
            catSet.add("기본");
        }
    }
    categories = Array.from(catSet);
}

function renderTabs() {
    var html = '';
    for (var i = 0; i < categories.length; i++) {
        var cat = categories[i];
        var activeClass = (cat === current_category) ? 'active' : '';
        html += '<li class="nav-item">';
        html += '  <a class="nav-link cat-tab ' + activeClass + '" href="#" data-cat="' + cat + '" style="cursor:pointer; margin-right:5px; margin-bottom:5px;">' + cat + '</a>';
        html += '</li>';
    }
    $('#category_tabs').html(html);
}

$("body").on('click', '.cat-tab', function(e) {
    e.preventDefault();
    $('.cat-tab').removeClass('active');
    $(this).addClass('active');
    current_category = $(this).data('cat');
    make_list();
});

function make_list() {
    var html = '';
    html += '<table class="table table-sm table-striped table-hover">';
    html += '<thead class="thead-dark"><tr>';
    html += '<th style="width:10%; text-align:center;">순번</th>';
    html += '<th style="width:70%;">제목</th>';
    html += '<th style="width:20%; text-align:center;">액션</th>';
    html += '</tr></thead><tbody>';
    
    var count = 0;
    for (var i = 0; i < current_data.length; i++) {
        var item = current_data[i];
        if (current_category !== "전체" && item.category !== current_category) continue;
        
        count++;
        html += '<tr>';
        html += '<td style="text-align:center; vertical-align:middle;">' + count + '</td>';
        html += '<td style="vertical-align:middle;">';
        if (current_category === "전체") {
            html += '<span class="badge badge-info mr-2">' + item.category + '</span>';
        }
        html += '<strong>' + item.display_title + '</strong>';
        html += '</td>';
        html += '<td style="text-align:center; vertical-align:middle;">';
        html += '<button class="btn btn-sm btn-outline-success play_btn" data-url="' + item.url + '" data-name="' + item.display_title + '"><i class="fa fa-play"></i> 재생</button>';
        html += '</td>';
        html += '</tr>';
    }
    
    if (count === 0) {
        html += '<tr><td colspan="3" style="text-align:center;" class="text-muted pt-4 pb-4">해당 카테고리에 미디어가 없습니다.</td></tr>';
    }
    
    html += '</tbody></table>';
    $('#list_div').html(html);
}

$("body").on('click', '.play_btn', function(e){
    e.preventDefault();
    var url = $(this).data('url');
    var name = $(this).data('name');
    var form = document.playform;
    var popupWidth = 980;
    var leftPos = screen.width - popupWidth;
    
    window.open('', 'play_popup', "location=no, directories=no,resizable=no,status=no,toolbar=no,menubar=no,width=" + popupWidth + ", height=560, top=100, left=" + leftPos);
    form.action = "/videojs";
    form.method = "post";
    form.target = 'play_popup'; 
    $('#play_title').val(name);
    $('#play_source_src').val(url);
    $('#play_source_type').val('video/mp2t'); 
    form.submit();
});
</script>
{% endblock %}
