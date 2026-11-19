<template>
  <div>
    <div class="hero"><div><h1>实收</h1><p>到货日实际点收行。按申报到货日落入日闭包；发生时刻仅审计不折日。结案后不可再写。</p></div><button @click="load">刷新</button></div>
    <div class="panel">
      <div class="row">
        <input v-model="form.receive_no" placeholder="实收号" />
        <input v-model="form.supplier_code" placeholder="供应商编码" />
        <input v-model="form.sku" placeholder="货号" />
        <input v-model="form.dock_day" placeholder="申报到货日 YYYY-MM-DD" />
        <input v-model="form.occurred_at" placeholder="发生时刻 ISO8601（仅审计）" />
        <input v-model.number="form.qty" type="number" placeholder="数量" />
        <button @click="add">登记实收</button>
      </div>
      <p class="msg">{{ err }}</p>
      <p class="okmsg">{{ ok }}</p>
      <table>
        <thead><tr><th>实收号</th><th>供应商</th><th>货号</th><th>日闭包(申报)</th><th>发生时刻(审计)</th><th>数量</th></tr></thead>
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
    ok.value = `已登记，落日闭包 ${res.dock_day}（口径 ${res.fold_rule}，未折日）`
    await load()
  } catch (e) { err.value = e.message }
}
onMounted(load)
</script>
