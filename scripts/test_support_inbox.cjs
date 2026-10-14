const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const {JSDOM} = require('jsdom');
const root = path.resolve(__dirname, '..');
const dom = new JSDOM('<div id="profiles"></div><div id="support" class="frappe-control"><div class="ma-support-ticket-html-root"></div></div>', {runScripts:'outside-only'});
const w = dom.window;
try {
 w.eval(fs.readFileSync(path.resolve(root,'../frappe/frappe/public/js/lib/jquery/jquery.min.js'),'utf8'));
 w.eval(`
   window.__ = s => s; window.mobile_app = {support_ticket_ui:{}};
   window.calls=[]; window.failLoad=false; window.pendingReply=null;
   window.tickets=[
    {name:'ST-TEST',ticket_doctype:'Support Ticket',subject:'Delivery help',description:'Where is my order?',status:'Open',label:'Orders',messages:[{from:'customer',body:'Please help with delivery',timestamp:'2026-10-13 12:00:00'}]},
    {name:'ST-TEST',ticket_doctype:'App Support Ticket',subject:'Older app ticket',description:'Legacy request',status:'Open',label:'General',messages:[]}
   ];
   window.frappe={provide:()=>{},require:(_,cb)=>cb(),datetime:{prettyDate:s=>s},show_alert:()=>{},confirm:(_,cb)=>cb(),call:opts=>{
    calls.push({method:opts.method,args:opts.args});
    if(opts.method.endsWith('get_support_tickets') || opts.method.endsWith('get_desk_support_inbox')) {
     if(failLoad) opts.error(); else opts.callback({message:{tickets, categories:window.serverCategories}});
    } else if(opts.method.endsWith('send_support_reply')) {
     window.pendingReply=()=>{
      const t=tickets.find(t=>t.name===opts.args.ticket_name&&t.ticket_doctype===opts.args.ticket_doctype);
      t.messages.push({from:'agent',body:opts.args.message});
      opts.callback({message:{tickets,ticket:t}});
     };
    } else if(opts.method.endsWith('set_support_ticket_status')) {
     const t=tickets.find(t=>t.name===opts.args.ticket_name&&t.ticket_doctype===opts.args.ticket_doctype);
     t.status=opts.args.status;opts.callback({message:{tickets,ticket:t}});
    }
   }};
   window.frm={doc:{name:'test-customer',full_name:'Test customer'},is_new:()=>false,fields_dict:{support_ticket_workspace:{$wrapper:$('#support')}}};
`);
 w.eval(fs.readFileSync(path.join(root,'mobile_app/public/js/mobile_app_support_ticket_ui.js'),'utf8'));
 w.mobile_app.support_ticket_ui.setup(w.frm);
 const $ = w.$;
 assert.equal($('#profiles').html(),'');
 assert.equal($('#support .ma-support-inbox__item').length,2);
 assert.equal($('.ma-support-inbox__item.is-active').length,1);
 assert.equal($('.ma-support-inbox__thread-title').text(),'Delivery help');
 $('textarea').val('Agent reply from ERP');
 $('.ma-support-inbox__send-btn').trigger('click');
 $('textarea').trigger($.Event('keydown',{key:'Enter'}));
 assert.equal(w.calls.filter(c=>c.method.endsWith('send_support_reply')).length,1);
 assert.equal(w.calls.at(-1).args.ticket_doctype,'Support Ticket');
 w.pendingReply();
 assert.equal($('.ma-support-inbox__bubble-row--agent').text(),'Agent reply from ERP');
 $('.ma-support-inbox__item').eq(1).trigger('click');
 assert.equal($('.ma-support-inbox__thread-title').text(),'Older app ticket');
 $('textarea').val('Legacy reply');
 $('.ma-support-inbox__send-btn').trigger('click');
 assert.equal(w.calls.at(-1).args.ticket_doctype,'App Support Ticket');
 w.pendingReply();
 $('.ma-support-inbox__item').first().trigger('click');
 $('[data-status="Closed"]').trigger('click');
 assert.equal($('textarea').prop('disabled'),true);
 w.failLoad=true; w.eval('refresh_tickets(frm)');
 assert.equal($('.ma-support-inbox__load-error').length,1);
 assert.equal($('.ma-support-inbox__item').length,2);
 w.failLoad=false; w.eval('refresh_tickets(frm)');
 assert.equal($('.ma-support-inbox__load-error').length,0);
 w.frm._ma_global_inbox=true;
 w.frm.doc={};
 w.tickets.forEach((t,i)=>{t.mobile_app_user='customer-'+i;t.user_name='Customer '+i;});
 w.eval('refresh_tickets(frm)');
 assert.equal(w.calls.at(-1).method,'mobile_app.api.support_ticket.get_desk_support_inbox');
 assert.equal($('.ma-support-inbox__item-name').first().text(),'Customer 0');
 $('.ma-support-inbox__item').eq(1).trigger('click');
 $('textarea').val('Central inbox reply');
 $('.ma-support-inbox__send-btn').trigger('click');
 assert.equal(w.calls.at(-1).args.mobile_app_user,'customer-1');
 assert.equal(w.calls.at(-1).args.ticket_doctype,'App Support Ticket');
 w.pendingReply();
 assert.equal(w.calls.at(-1).method,'mobile_app.api.support_ticket.get_desk_support_inbox');
 assert.equal($('.ma-support-inbox__item').length,2);
 $('.ma-support-inbox__filter--status').val('Closed').trigger('change');
 assert.equal(w.calls.at(-1).args.status,'Closed');
 // A category found only on another server page must remain selectable.
 w.serverCategories=['Orders','General','Payment'];
 w.eval('refresh_tickets(frm)');
 assert.equal($('.ma-support-inbox__filter--category option[value="Payment"]').length,1);
 w.frm._ma_start=20;
 $('.ma-support-inbox__filter--category').val('Payment').trigger('change');
 assert.equal(w.calls.at(-1).method,'mobile_app.api.support_ticket.get_desk_support_inbox');
 assert.equal(w.calls.at(-1).args.category,'Payment');
 assert.equal(w.calls.at(-1).args.start,0);
 w.tickets=[];w.serverCategories=[];
 w.eval('refresh_tickets(frm)');
 assert.equal($('.ma-support-inbox__filter--category').val(),'Payment');
 assert.equal(w.frm._ma_support_ticket_filters.category,'Payment');
 console.log('Support inbox DOM checks passed: both sources, replies, duplicate-send prevention, closed tickets, load errors, central inbox customer identity and filters.');
} finally {w.close()}
