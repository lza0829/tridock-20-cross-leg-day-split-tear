<template>
  <div>
    <div class="hero"><div><h1>实收</h1><p>到货日实际点收行。同供应商+货号以最先完整落库行的业务日为锚，本行改挂锚日（申报日留痕）；锚日封死后改挂进隔离区、HTTP 非 200。发生时刻仅审计不定锚。</p></div><button @click="load">刷新</button></div>
    <div class="panel">
      <div class="row">
        <input v-model="form.receive_no" placeholder="实收号" />
        <input v-model="form.supplier_code" placeholder="供应商编码" />
        <input v-model="form.sku" placeholder="货号" />
        <input v-model="form.dock_day" placeholder="申报业务日 YYYY-MM-DD" />
        <input v-model="form.occurred_at" placeholder="发生时刻 ISO8601（仅审计）" />
        <input v-model.number="form.qty" type="number" placeholder="数量" />
        <button @click="add">登记实收</button>
      </div>
      <p class="msg">{{ err }}</p>
      <p class="okmsg">{{ ok }}</p>
      <table>
        <thead><tr><th>实收号</th><th>供应商</th><th>货号</th><th>锚日闭包</th><th>发生时刻(审计)</th><th>数量</th></tr></thead>
        <tbody>
          <tr v-for="r in rows" :key="r.id">
            <td>{{ r.receive_no }}</td><td>{{ r.supplier_code }}</td><td>{{ r.sku }}</td>
            <td>{{ r.dock_day }}</td><td>{{ r.occurred_at }}</td><td>{{ r.qty }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api'
const rows = ref([])
const err = ref('')
const ok = ref('')
const form = ref({ receive_no: '', supplier_code: 'SUP-A', sku: '', dock_day: '2026-11-18', occurred_at: '', qty: 1 })
async function load() { rows.value = await api('/receives') }
async function add() {
  err.value = ''; ok.value = ''
  try {
    const body = { ...form.value }
    if (!body.occurred_at) delete body.occurred_at
    const res = await api('/receives', { method: 'POST', body: JSON.stringify(body) })
    ok.value = res.folded
      ? `已改挂至锚日 ${res.anchor_day}（申报 ${res.declared_dock_day}，口径 ${res.fold_rule}）`
      : `已落锚日闭包 ${res.anchor_day}（口径 ${res.fold_rule}）`
    await load()
  } catch (e) { err.value = e.message }
}
onMounted(load)
</script>
