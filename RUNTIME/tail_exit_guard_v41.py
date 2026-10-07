def exit_signal(entry_price,current_price,entry_liq,current_liq,buys,sells,age_seconds,interest=1.0):
    if entry_price<=0:return {'exit':False,'reason':'NO_ENTRY_PRICE'}
    ret=(current_price/entry_price-1)*100
    liq_drop=1-(current_liq/max(1,entry_liq));sell_share=sells/max(1,buys+sells)
    fade=max(0.0,min(1.0,1.0-interest))
    hard_stop=-14+3*fade;fast_stop=-8+2*fade
    reasons=[]
    if ret<=hard_stop:reasons.append('HARD_PRICE_STOP')
    if age_seconds<=90 and ret<=fast_stop:reasons.append('FAST_COLLAPSE')
    if liq_drop>=.20:reasons.append('LIQUIDITY_DRAIN')
    if sell_share>=.68 and ret<=-5:reasons.append('SELL_PRESSURE')
    return {'exit':bool(reasons),'reason':'+'.join(reasons) if reasons else 'HOLD','return_pct':round(ret,3),
            'liq_drop_pct':round(liq_drop*100,2),'sell_share':round(sell_share,3),'hard_stop_pct':hard_stop}
